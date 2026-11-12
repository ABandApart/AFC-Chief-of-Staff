"""Sync outreach targets with the Notion Leads database (`PRD-notion-integration.md` N8).

    uv run python -m agents.notion.leads_sync pull     # 05:30: Notion edits, starts, tasks
    uv run python -m agents.notion.leads_sync push     # 06:20: mirror every target to Leads
    uv run python -m agents.notion.leads_sync pull --dry-run

**pull** (before `outreach-daily` at 05:45, so new sequences get their packets and
Gmail drafts the same morning):

1. Takes the operator's edits from Leads: Status (Replied, Call booked, Client,
   Not a fit, Opted out), Start on, and, while the target is a `candidate`, Stage
   and Function state. Each field merges against the value both sides last agreed
   on, as N-S17 does for To-Do rows: a Notion change wins. A value the CoS can't
   take is refused and reported, and the next push restores it.
2. Ending a live sequence (Replied, Not a fit, Opted out) skips its unsent
   touches. Nothing is deleted: Not a fit and Opted out are statuses.
3. Starts each `candidate` whose Start on is today or earlier, through
   `outreach_intake.decide(id, "work")`. A target whose original trigger is older
   than slot 1's window is re-anchored on today first, so the arc runs from the
   start day instead of opening with expired touches (the rule 0023 set for Gate 0
   approvals).
4. Creates a task for every touch whose window is open and that has no task yet
   (N-D17), through `task_tinder.create_outreach_task`. The To-Do sync pushes it.
5. Posts one #tasks message when there is something to say: sequences started,
   starts refused, values refused, or Gmail drafts left for ended sequences.

**push** writes one Leads row per target (Channel = Outreach), skipping rows whose
CoS-written columns are unchanged. It never writes Notes or Next step, and never
touches a Docent row. It also applies any Notion edits made since the pull.

Every non-dry run writes one `agent_runs` row (`agents/notion/ledger.py`).
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from psycopg.rows import dict_row

from agents._lib.notion_client import NotionError, content_hash
from agents.notion import leads_schema as leads
from agents.notion.todo_schema import merge_field

DEFAULT_OWNER = "operator"  # what Task Tinder gives accepted tasks
SLOT_1_CLOSES = 7  # days after trigger_date (packet.SLOT_WINDOWS[1])

_TARGETS_SQL = """
    WITH nxt AS (
        SELECT DISTINCT ON (target_id) id, target_id, due_date
        FROM outreach_touches
        WHERE sent_at IS NULL AND skipped_at IS NULL
        ORDER BY target_id, slot
    )
    SELECT t.id, t.status, t.company_name, t.company_domain, t.company_url, t.sector,
           t.headcount, t.hq_location, t.ownership_type, t.total_raised_usd,
           t.last_round_type, t.last_round_at, t.founded_year,
           t.contact_name, t.contact_role, t.contact_email, t.contact_linkedin_url,
           t.stage, t.function_state, t.start_on,
           (SELECT max(sent_at) FROM outreach_touches s WHERE s.target_id = t.id)
               AS last_contacted,
           CASE WHEN t.status = 'in_sequence' THEN nxt.due_date END AS next_touch,
           p.subject_line, p.body_filled, p.ready AS packet_ready,
           ns.notion_page_id, ns.content_hash, ns.synced_fields
    FROM outreach_targets t
    LEFT JOIN nxt ON nxt.target_id = t.id AND t.status = 'in_sequence'
    LEFT JOIN LATERAL (
        SELECT subject_line, body_filled, ready FROM outreach_packets
        WHERE touch_id = nxt.id ORDER BY assembled_at DESC, id DESC LIMIT 1
    ) p ON true
    LEFT JOIN notion_sync ns ON ns.entity_table = %s AND ns.entity_id = t.id::text
    ORDER BY t.id
"""

_DUE_TOUCHES_SQL = """
    SELECT tc.id, tc.slot, tc.due_date, t.company_name, t.contact_name
    FROM outreach_touches tc
    JOIN outreach_targets t ON t.id = tc.target_id
    WHERE t.status = 'in_sequence'
      AND tc.sent_at IS NULL AND tc.skipped_at IS NULL
      AND tc.window_opens <= %(today)s AND tc.window_closes >= %(today)s
      AND (tc.snoozed_until IS NULL OR tc.snoozed_until <= %(today)s)
      AND NOT EXISTS (SELECT 1 FROM task_candidates c
                      WHERE c.source_type = 'outreach_touch'
                        AND c.source_ref = 'touch:' || tc.id)
    ORDER BY tc.due_date, tc.id
"""

_PULL_SQL = {
    "status": "UPDATE outreach_targets SET status = %s WHERE id = %s",
    "start_on": "UPDATE outreach_targets SET start_on = %s WHERE id = %s",
    "stage": "UPDATE outreach_targets SET stage = %s WHERE id = %s AND status = 'candidate'",
    "function_state": "UPDATE outreach_targets SET function_state = %s "
                      "WHERE id = %s AND status = 'candidate'",
}


@dataclass
class Step:
    """What one run does for one target (pure data, so a dry run can print it)."""

    target_id: int
    company: str
    status: str
    write: str  # create | update | skip
    page_id: str | None
    props: dict[str, Any]
    digest: str
    agreed: dict[str, Any]
    decisions: dict[str, str] = field(default_factory=dict)  # push|pull|agree|refuse
    pulls: dict[str, Any] = field(default_factory=dict)
    refused: list[str] = field(default_factory=list)


def plan(targets: list[dict[str, Any]], pages: dict[str, dict[str, Any]], *,
         capacity_free: bool) -> list[Step]:
    """Decide each target's per-field merge and row write (pure)."""
    steps = []
    for t in targets:
        pv = leads.pg_values(t)
        owned = leads.owned_fields(t)
        facts = leads.fact_props(t, capacity_free=capacity_free)
        digest = content_hash(facts)
        page = pages.get(t["notion_page_id"]) if t.get("notion_page_id") else None
        if page is None:
            steps.append(Step(t["id"], t["company_name"], t["status"], "create", None,
                              {**leads.field_props(pv, owned), **facts}, digest,
                              {f: pv[f] for f in owned}))
            continue
        nv, before = leads.notion_values(page), dict(t.get("synced_fields") or {})
        step = Step(t["id"], t["company_name"], t["status"], "skip", page["id"], {},
                    digest, dict(before))
        pushes = []
        for name in owned:
            decision = merge_field(pv[name], nv[name], before.get(name),
                                   has_agreed=name in before)
            if decision == "pull" and (why := leads.refusal(name, nv[name])):
                decision = "refuse"
                step.refused.append(why)
            step.decisions[name] = decision
            if decision == "pull":
                step.pulls[name] = nv[name]
                step.agreed[name] = nv[name]
            elif decision in ("push", "refuse"):
                pushes.append(name)
                step.agreed[name] = pv[name]
            else:
                step.agreed[name] = pv[name]
        step.props = leads.field_props(pv, pushes)
        if digest != t.get("content_hash"):
            step.props.update(facts)
        if step.props:
            step.write = "update"
        steps.append(step)
    return steps


def start_problem(target: dict[str, Any], capacity: dict[str, Any]) -> str | None:
    """Why a scheduled start can't happen, or None (pure)."""
    from agents._lib import outreach_intake

    return (outreach_intake.work_blocks(target)
            or outreach_intake.capacity_blocks(capacity,
                                               is_reengagement=target["is_reengagement"]))


def needs_reanchor(trigger_date: date, today: date) -> bool:
    """Whether slot 1's window closed before the start day (pure)."""
    return trigger_date + timedelta(days=SLOT_1_CLOSES) < today


def touch_title(touch: dict[str, Any]) -> str:
    return f"Outreach: {touch['company_name']}, send touch {touch['slot']}"


def report(started: list[str], not_ready: list[str], refused: list[str],
           drafts: list[str], tasks: int) -> str | None:
    """The #tasks message, or None when there's nothing to say (pure)."""
    lines = []
    if started:
        lines.append(f"Outreach: {len(started)} sequence(s) started ({', '.join(started)})")
    if not_ready:
        lines.append("Not started: " + "; ".join(not_ready))
    if refused:
        lines.append("Leads values not taken: " + "; ".join(refused))
    if drafts:
        lines.append("Delete the unsent Gmail draft(s) for: " + ", ".join(drafts))
    if not lines:
        return None
    if tasks:
        lines.append(f"{tasks} outreach task(s) added to To-Do")
    return "\n".join(lines)


# --- I/O ---------------------------------------------------------------------------


def _pages(client: Any) -> tuple[dict[str, dict[str, Any]], str]:
    """Live Outreach rows by page id, and the data source id. Fails if the Leads
    columns the CoS needs aren't there yet."""
    sources = client.retrieve_database(leads.DATABASE_ID).get("data_sources") or []
    if not sources:
        raise RuntimeError("the Leads database has no data source")
    source = sources[0]["id"]
    problems = leads.schema_problems(client.retrieve_data_source(source).get("properties") or {})
    if problems:
        raise RuntimeError("Leads isn't ready: " + "; ".join(problems))
    rows = client.iter_query(source, filter={"property": "Channel",
                                             "select": {"equals": leads.CHANNEL}})
    return {p["id"]: p for p in rows if not p.get("in_trash") and not p.get("archived")}, source


def _load(connection: Any, client: Any) -> tuple[list[dict], dict, str, dict]:
    from agents._lib import outreach_intake

    with connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_TARGETS_SQL, (leads.SYNC_TABLE,))
            targets = cur.fetchall()
        capacity = outreach_intake.read_capacity(conn)
    pages, source = _pages(client)
    return targets, pages, source, capacity


def _free(capacity: dict[str, Any]) -> bool:
    return capacity["cold_live"] < capacity["cold_ceiling"]


def _record(conn: Any, target_id: int, page_id: str, digest: str | None,
            agreed: dict[str, Any], pushed: bool) -> None:
    conn.execute(
        """
        INSERT INTO notion_sync (entity_table, entity_id, notion_page_id, content_hash,
                                 synced_fields, last_pushed_at, last_pulled_at)
        VALUES (%s, %s, %s, %s, %s, CASE WHEN %s THEN now() END, now())
        ON CONFLICT (entity_table, entity_id) DO UPDATE SET
            notion_page_id = EXCLUDED.notion_page_id,
            content_hash = COALESCE(EXCLUDED.content_hash, notion_sync.content_hash),
            synced_fields = EXCLUDED.synced_fields,
            last_pushed_at = COALESCE(EXCLUDED.last_pushed_at, notion_sync.last_pushed_at),
            last_pulled_at = now()
        """,
        (leads.SYNC_TABLE, str(target_id), page_id, digest, json.dumps(agreed), pushed),
    )


def _apply_pulls(connection: Any, step: Step) -> list[str]:
    """Write one target's Notion edits to Postgres. Returns the companies whose
    ended sequences left an unsent Gmail draft."""
    drafts: list[str] = []
    with connection() as conn, conn.transaction():
        for name, value in step.pulls.items():
            if name == "status":
                value = leads.SETTABLE[value]
            conn.execute(_PULL_SQL[name], (value, step.target_id))
            if (name == "status" and step.status == "in_sequence"
                    and value in leads.ENDS_SEQUENCE):
                rows = conn.execute(
                    "UPDATE outreach_touches SET skipped_at = now(), skip_reason = %s "
                    "WHERE target_id = %s AND sent_at IS NULL AND skipped_at IS NULL "
                    "RETURNING gmail_draft_id",
                    (f"notion_{value}", step.target_id)).fetchall()
                if any(r[0] for r in rows):
                    drafts.append(step.company)
    return drafts


def _sync(client: Any, connection: Any, *, write_rows: bool) -> tuple[dict[str, int], list, list]:
    """Apply Notion edits; with `write_rows`, also write Leads rows. Returns counts,
    refusals, and drafts to delete."""
    targets, pages, source, capacity = _load(connection, client)
    counts = {"pulled": 0, "refused": 0, "created": 0, "updated": 0}
    refused: list[str] = []
    drafts: list[str] = []
    for step in plan(targets, pages, capacity_free=_free(capacity)):
        if step.pulls:
            drafts += _apply_pulls(connection, step)
            counts["pulled"] += len(step.pulls)
        refused += [f"{step.company}: {r}" for r in step.refused]
        counts["refused"] += len(step.refused)
        if not write_rows:
            if step.page_id and step.pulls:
                agreed = dict(next(t for t in targets if t["id"] == step.target_id)
                              .get("synced_fields") or {})
                agreed.update(step.pulls)
                with connection() as conn:
                    _record(conn, step.target_id, step.page_id, None, agreed, False)
            continue
        page_id = step.page_id
        if step.write == "create":
            page_id = client.create_page(
                {"type": "data_source_id", "data_source_id": source}, step.props)["id"]
            counts["created"] += 1
        elif step.write == "update":
            client.update_page(page_id, step.props)
            counts["updated"] += 1
        with connection() as conn:
            _record(conn, step.target_id, page_id, step.digest, step.agreed,
                    step.write != "skip")
    return counts, refused, drafts


def _start_scheduled(connection: Any, today: date) -> tuple[list[str], list[str]]:
    """Start every candidate whose Start on has come. Returns started companies and
    the reasons others weren't started."""
    from agents._lib import outreach_intake

    with connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute("SELECT * FROM v_outreach_scored WHERE status = 'candidate' "
                        "AND id IN (SELECT id FROM outreach_targets WHERE start_on <= %s) "
                        "ORDER BY id", (today,))
            due = cur.fetchall()
    started, not_ready = [], []
    for t in due:
        with connection() as conn:
            problem = start_problem(t, outreach_intake.read_capacity(conn))
        if problem:
            not_ready.append(f"{t['company_name']}: {problem.split(' — ')[0]}")
            continue
        if needs_reanchor(t["trigger_date"], today):
            with connection() as conn:
                conn.execute("UPDATE outreach_targets SET trigger_date = %s "
                             "WHERE id = %s AND status = 'candidate'", (today, t["id"]))
        try:
            result = outreach_intake.decide(t["id"], "work", today=today)
        except (outreach_intake.CapacityFullError, outreach_intake.NotReadyToWorkError) as e:
            not_ready.append(f"{t['company_name']}: {e}")
            continue
        if result and result.get("status") == "in_sequence":
            started.append(t["company_name"])
    return started, not_ready


def _touch_tasks(connection: Any, create: Any, today: date, owner: str) -> int:
    with connection() as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(_DUE_TOUCHES_SQL, {"today": today})
            touches = cur.fetchall()
    made = 0
    for tc in touches:
        due = max(tc["due_date"], today)
        who = f" to {tc['contact_name']}" if tc.get("contact_name") else ""
        if create(touch_id=tc["id"], title=touch_title(tc), owner=owner, due_date=due,
                  evidence=f"Touch {tc['slot']}{who}. The draft is in Gmail; "
                           f"the message is on the Leads row."):
            made += 1
    return made


def run_pull(client: Any, *, connection: Any, create: Any, post: Any, today: date,
             owner: str = DEFAULT_OWNER) -> str:
    """The 05:30 run. `create` is `task_tinder.create_outreach_task`; `post` sends
    the #tasks message and returns True or False."""
    counts, refused, drafts = _sync(client, connection, write_rows=False)
    started, not_ready = _start_scheduled(connection, today)
    tasks = _touch_tasks(connection, create, today, owner)
    text = report(started, not_ready, refused, drafts, tasks)
    posted = "nothing to post"
    if text:
        posted = "posted to #tasks" if post(text) else "post to #tasks FAILED"
    return (f"OK    {counts['pulled']} field(s) taken from Leads, {counts['refused']} "
            f"refused; {len(started)} sequence(s) started, {len(not_ready)} not started; "
            f"{tasks} outreach task(s) created; {posted}")


def run_push(client: Any, *, connection: Any) -> str:
    """The 06:20 run."""
    counts, refused, _ = _sync(client, connection, write_rows=True)
    return (f"OK    Leads: {counts['created']} row(s) created, {counts['updated']} "
            f"updated; {counts['pulled']} field(s) taken from Leads, "
            f"{counts['refused']} refused")


def _post(text: str) -> bool:
    from agents._lib import discord_rest
    from agents.discord_bot.config import TASKS_CHANNEL_ID

    return discord_rest.post(TASKS_CHANNEL_ID, text)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync outreach targets with Notion Leads.")
    parser.add_argument("mode", choices=("pull", "push"))
    parser.add_argument("--dry-run", action="store_true",
                        help="print what each target would do; write nothing")
    parser.add_argument("--manual", action="store_true",
                        help="record the run as manual rather than scheduled")
    parser.add_argument("--date", type=date.fromisoformat, default=None,
                        help="act as if today were this date (testing only)")
    args = parser.parse_args()
    today = args.date or date.today()

    from agents._lib import db, task_tinder
    from agents._lib.notion_client import NotionClient
    from agents.notion.ledger import notion_run

    try:
        client = NotionClient.from_keychain()
        if args.dry_run:
            targets, pages, _, capacity = _load(db.connection, client)
            for step in plan(targets, pages, capacity_free=_free(capacity)):
                merged = ", ".join(f"{k} {v}" for k, v in step.decisions.items()
                                   if v != "agree") or "all agree"
                print(f"target #{step.target_id} {step.company[:40]}: {merged}; "
                      f"row {step.write}" + (f"; refused: {step.refused}" if step.refused else ""))
            due = [t for t in targets if t["status"] == "candidate" and t.get("start_on")
                   and t["start_on"] <= today]
            print(f"PLAN  {len(due)} scheduled start(s) due on {today}")
            return 0
        label = f"leads_{args.mode}"
        with notion_run(label, trigger_kind="manual" if args.manual else "scheduled"):
            if args.mode == "pull":
                print(run_pull(client, connection=db.connection,
                               create=task_tinder.create_outreach_task, post=_post,
                               today=today))
            else:
                print(run_push(client, connection=db.connection))
    except (RuntimeError, NotionError) as e:
        print(f"FAIL  {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

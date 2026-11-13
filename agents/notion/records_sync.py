"""Take Outcomes, Capture, and Sources inputs from Notion (`PRD-notion-integration.md` N9 I5).

Part of the daily `notion-inputs-sync` loop (05:15). It replaces three Discord
inputs: `/outcome`, #capture, and Tartt control in #briefing. `run` handles the
three databases one after another. A database that isn't set up, or whose
columns don't match `records_schema`, is skipped with a notice, so one missing
database doesn't stop the other two.

**Outcomes.** A row with a title and no CoS id is imported into `outcomes` once:
Type, Value, Company (matched as the `/outcome` type-ahead would,
`_lib/outcomes.match_company`), and Date as `recorded_at`. The CoS then writes the
row's CoS id and never reads it again, so KR history can't change silently. A
contract type with no matching company is refused, as the database CHECK
`outcomes_engagement_has_client_ck` would refuse it.

**Capture.** Each row with text that isn't Ingested runs through
`ingest.ingest_note`, which skips a note it has seen before. Success ticks
Ingested and writes Result. A failure leaves Ingested unticked, so the next run
retries it, and is reported.

**Sources.** Two-way. Every `sources` row and every current Tartt interest has a
row. On a feed row the operator owns Active, Every, and URL; on an interest row,
Active. Each owned field merges against the value both sides last agreed on
(`todo_schema.merge_field`): a Notion change wins. A row added by hand becomes a
feed (`tartt_control.add_feed`) or, with Kind = interest, an interest
(`add_interest`). Unticking Active on an interest removes it (`remove_interest`);
ticking it again adds it back.

**Operator check (N-D24).** An input or change is taken only from a page last
edited by the operator or the CoS (`operator.foreign_edit`). Anything else is
refused and reported, and on Sources the CoS's value is written back.

Every Notion write is skipped when the CoS-written columns are unchanged.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from psycopg.rows import dict_row

from agents._lib import outcomes as outcomes_lib
from agents._lib.notion_client import NotionError, content_hash
from agents.notion import operator
from agents.notion import records_schema as rs
from agents.notion.todo_schema import merge_field

FOREIGN = "edited by someone other than the operator; not taken"


class _RefusedError(Exception):
    """Raised inside an outcome's transaction to roll back a refused import."""


# --- shared I/O ----------------------------------------------------------------------


def _open(client: Any, connection: Any, key: str, label: str, columns: dict[str, str],
          options: dict[str, tuple[str, ...]]) -> tuple[str, list[dict[str, Any]]]:
    """A database's data source id and live rows. Fails if it isn't set up or its
    columns don't match, before anything is written."""
    with connection() as conn:
        database_id = operator.schema_id(conn, key)
    sources = client.retrieve_database(database_id).get("data_sources") or []
    if not sources:
        raise RuntimeError(f"the {label} database has no data source")
    source = sources[0]["id"]
    problems = rs.schema_problems(
        client.retrieve_data_source(source).get("properties") or {}, columns, options)
    if problems:
        raise RuntimeError(f"{label} isn't ready: " + "; ".join(problems))
    return source, [p for p in client.iter_query(source)
                    if not p.get("in_trash") and not p.get("archived")]


def _record(conn: Any, table: str, entity_id: Any, page_id: str, digest: str | None,
            agreed: dict[str, Any] | None, pushed: bool) -> None:
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
        (table, str(entity_id), page_id, digest,
         json.dumps(agreed) if agreed is not None else None, pushed),
    )


def _mapped(connection: Any, table: str) -> dict[str, dict[str, Any]]:
    """`notion_sync` rows of one entity table, by entity id."""
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT entity_id, notion_page_id, content_hash, synced_fields "
                    "FROM notion_sync WHERE entity_table = %s", (table,))
        return {r["entity_id"]: r for r in cur.fetchall()}


def _label(page: dict[str, Any], name: str) -> str:
    return repr(rs.text_of(page, name)[:60] or "(untitled)")


# --- Outcomes ------------------------------------------------------------------------


def _outcomes(client: Any, connection: Any, allowed: frozenset[str], today: date,
              notices: list[str]) -> str:
    _, pages = _open(client, connection, rs.OUTCOMES_KEY, "Outcomes",
                     rs.OUTCOMES_COLUMNS, rs.OUTCOMES_OPTIONS)
    by_page = {r["notion_page_id"]: int(eid)
               for eid, r in _mapped(connection, rs.OUTCOMES_TABLE).items()}
    imported = refused = 0
    for page in pages:
        if rs.cos_id(page) is not None:
            continue  # imported already: never re-read
        if page["id"] in by_page:
            # Imported, but the CoS id write didn't land. Write it; don't import twice.
            client.update_page(page["id"], rs.outcome_written(by_page[page["id"]]))
            continue
        fields = rs.outcome_fields(page)
        if not fields["description"]:
            continue  # Notion makes empty rows easily; import one once it has a title
        label = _label(page, "Outcome")
        why = FOREIGN if operator.foreign_edit(page, allowed) else rs.outcome_refusal(fields)
        recorded_on = rs.outcome_date(fields)
        if why is None and recorded_on and recorded_on > today:
            why = f"Date {recorded_on} is in the future"
        if why:
            notices.append(f"Outcomes: {label} not imported: {why}")
            refused += 1
            continue
        if recorded_on is None and page.get("created_time"):
            recorded_on = date.fromisoformat(page["created_time"][:10])
        written: dict[str, Any] = {}
        try:
            with connection() as conn, conn.transaction(), \
                    conn.cursor(row_factory=dict_row) as cur:
                found, target_id, problem = outcomes_lib.match_company(cur, fields["company"])
                if why := rs.company_refusal(fields["type"], problem):
                    raise _RefusedError(why)
                outcome_id = outcomes_lib.insert(
                    cur, outcome_type=fields["type"], description=fields["description"],
                    value=fields["value"], client=found, target_id=target_id,
                    recorded_on=recorded_on)
                written = rs.outcome_written(outcome_id)
                _record(conn, rs.OUTCOMES_TABLE, outcome_id, page["id"],
                        content_hash(written), None, True)
        except _RefusedError as e:
            notices.append(f"Outcomes: {label} not imported: {e}")
            refused += 1
            continue
        if problem:
            notices.append(f"Outcomes: {label} imported without a company: {problem}")
        client.update_page(page["id"], written)
        imported += 1
    return f"Outcomes {imported} imported, {refused} refused"


# --- Capture -------------------------------------------------------------------------


def default_ingest(notes: list[tuple[str, str]]) -> list[str | BaseException]:
    """Ingest notes as the bot's #capture does, in one event loop. Returns each
    note's result ('captured' or 'repost') or the exception it raised."""
    from agents._lib import ingest

    _configure_cognee()

    async def _all() -> list[str | BaseException]:
        out: list[str | BaseException] = []
        for text, ref in notes:
            try:
                out.append(await ingest.ingest_note(text, source_ref=ref,
                                                    source_type="notion"))
            except Exception as e:  # noqa: BLE001  (reported per note)
                out.append(e)
        return out

    return asyncio.run(_all())


_cognee_ready = False


def _configure_cognee() -> None:
    """Configure cognee once per process, before any graph read or write, as
    `discord_bot/run.py` does at bot startup."""
    global _cognee_ready
    if not _cognee_ready:
        from agents._lib import cognee_setup

        cognee_setup.configure_cognee()
        _cognee_ready = True


def _capture(client: Any, connection: Any, allowed: frozenset[str], ingest: Any,
             notices: list[str]) -> str:
    _, pages = _open(client, connection, rs.CAPTURE_KEY, "Capture",
                     rs.CAPTURE_COLUMNS, rs.CAPTURE_OPTIONS)
    hashes = {eid: r["content_hash"] for eid, r in _mapped(connection, rs.CAPTURE_TABLE).items()}
    pending = []
    for page in pages:
        if not rs.capture_pending(page):
            continue
        if operator.foreign_edit(page, allowed):
            notices.append(f"Capture: {_label(page, 'Note')} {FOREIGN}")
            continue
        pending.append(page)
    if not pending:
        return "Capture 0 ingested"
    results = ingest([(rs.capture_text(p), f"notion:{p['id']}") for p in pending])
    done = failed = 0
    for page, result in zip(pending, results, strict=True):
        if isinstance(result, BaseException):
            props = rs.capture_written(ingested=False, result=f"Not ingested yet: {result}")
            notices.append(f"Capture: {_label(page, 'Note')} not ingested: {result}")
            failed += 1
        else:
            props = rs.capture_written(ingested=True, result=rs.RESULT_TEXT.get(result, result))
            done += 1
        digest = content_hash(props)
        if hashes.get(page["id"]) != digest or not isinstance(result, BaseException):
            client.update_page(page["id"], props)
        with connection() as conn:
            _record(conn, rs.CAPTURE_TABLE, page["id"], page["id"], digest, None, True)
    return f"Capture {done} ingested, {failed} failed"


# --- Sources: the pure plan ----------------------------------------------------------


@dataclass
class FeedStep:
    """What one run does for one `sources` row (pure data)."""

    source_id: int
    name: str
    write: str  # create | update | skip
    page_id: str | None
    props: dict[str, Any]
    digest: str
    agreed: dict[str, Any]
    pulls: dict[str, Any] = field(default_factory=dict)
    refused: list[str] = field(default_factory=list)


def plan_feeds(feeds: list[dict[str, Any]], pages: dict[str, dict[str, Any]],
               allowed: frozenset[str]) -> list[FeedStep]:
    """Decide each feed's per-field merge and row write (pure)."""
    steps = []
    for s in feeds:
        pv, facts = rs.feed_pg_values(s), rs.feed_facts(s)
        digest = content_hash(facts)
        page = pages.get(s["notion_page_id"]) if s.get("notion_page_id") else None
        if page is None:
            steps.append(FeedStep(s["id"], s["name"], "create", None,
                                  {**facts, **rs.owned_props(pv, rs.FEED_OWNED)}, digest,
                                  dict(pv)))
            continue
        nv, before = rs.feed_notion_values(page), dict(s.get("synced_fields") or {})
        foreign = operator.foreign_edit(page, allowed)
        step = FeedStep(s["id"], s["name"], "skip", page["id"], {}, digest, dict(before))
        pushes = []
        for name in rs.FEED_OWNED:
            decision = merge_field(pv[name], nv[name], before.get(name),
                                   has_agreed=name in before)
            if decision == "pull":
                why = FOREIGN if foreign else rs.feed_refusal(name, nv[name])
                if why:
                    decision = "refuse"
                    step.refused.append(f"{rs.PROPERTY[name]} {why}")
            if decision == "pull":
                step.pulls[name] = step.agreed[name] = nv[name]
            else:
                step.agreed[name] = pv[name]
                if decision != "agree":
                    pushes.append(name)
        step.props = rs.owned_props(pv, pushes)
        if digest != s.get("content_hash"):
            step.props.update(facts)
        if step.props:
            step.write = "update"
        steps.append(step)
    return steps


@dataclass
class InterestStep:
    """What one run does for one interest topic (pure data)."""

    topic: str
    write: str  # create | update | skip | forget (drop the mapping)
    page_id: str | None
    props: dict[str, Any]
    digest: str
    agreed: dict[str, Any]
    pull: bool | None = None  # True: add the interest; False: remove it
    refused: list[str] = field(default_factory=list)


def plan_interests(topics: list[str], mapped: dict[str, dict[str, Any]],
                   pages: dict[str, dict[str, Any]],
                   allowed: frozenset[str]) -> list[InterestStep]:
    """Decide each interest's Active merge and row write (pure). `mapped` is the
    `notion_sync` `interests` rows by topic. Topics compare without case, as
    `tartt_control.add_interest` does."""
    current = {t.strip().casefold() for t in topics}
    seen: set[str] = set()
    steps = []
    for topic, row in mapped.items():
        seen.add(topic.casefold())
        pg = {"active": topic.casefold() in current}
        facts = rs.interest_facts(topic)
        digest = content_hash(facts)
        page = pages.get(row["notion_page_id"])
        if page is None:
            if pg["active"]:
                steps.append(InterestStep(topic, "create", None,
                                          {**facts, **rs.owned_props(pg, rs.INTEREST_OWNED)},
                                          digest, pg))
            else:
                steps.append(InterestStep(topic, "forget", None, {}, digest, {}))
            continue
        before = dict(row.get("synced_fields") or {})
        nv = {"active": rs.checkbox_of(page, "Active")}
        step = InterestStep(topic, "skip", page["id"], {}, digest, dict(pg))
        decision = merge_field(pg["active"], nv["active"], before.get("active"),
                               has_agreed="active" in before)
        if decision == "pull" and operator.foreign_edit(page, allowed):
            decision = "refuse"
            step.refused.append(f"Active {FOREIGN}")
        if decision == "pull":
            step.pull = nv["active"]
            step.agreed = nv
        elif decision != "agree":
            step.props = rs.owned_props(pg, rs.INTEREST_OWNED)
        if digest != row.get("content_hash"):
            step.props.update(facts)
        if step.props:
            step.write = "update"
        steps.append(step)
    for topic in topics:
        if topic.strip().casefold() not in seen:
            seen.add(topic.strip().casefold())
            pg = {"active": True}
            facts = rs.interest_facts(topic.strip())
            steps.append(InterestStep(topic.strip(), "create", None,
                                      {**facts, **rs.owned_props(pg, rs.INTEREST_OWNED)},
                                      content_hash(facts), pg))
    return steps


def new_rows(pages: dict[str, dict[str, Any]], mapped_pages: set[str]) -> list[dict[str, Any]]:
    """Rows added by hand: not mapped, no CoS id, and a Name (pure)."""
    return [p for pid, p in pages.items() if pid not in mapped_pages
            and rs.cos_id(p) is None and rs.text_of(p, "Name")]


# --- Sources: I/O --------------------------------------------------------------------

_FEEDS_SQL = """
    SELECT s.id, s.name, s.url, s.source_kind, s.poll_interval_hours, s.active,
           ns.notion_page_id, ns.content_hash, ns.synced_fields
    FROM sources s
    LEFT JOIN notion_sync ns ON ns.entity_table = %s AND ns.entity_id = s.id::text
    ORDER BY s.id
"""

_FEED_PULL_SQL = {
    "active": "UPDATE sources SET active = %s WHERE id = %s",
    "every": "UPDATE sources SET poll_interval_hours = %s WHERE id = %s",
    "url": "UPDATE sources SET url = %s WHERE id = %s",
}


def _feeds(connection: Any) -> list[dict[str, Any]]:
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute(_FEEDS_SQL, (rs.SOURCES_TABLE,))
        return cur.fetchall()


def _import_row(client: Any, connection: Any, tartt: Any, page: dict[str, Any],
                feeds: list[dict[str, Any]], interests: dict[str, dict[str, Any]],
                pages: dict[str, dict[str, Any]]) -> tuple[str, str | None]:
    """Make a hand-added Sources row a feed or an interest. Returns what was done
    ('feed', 'interest', or 'refused') and a notice, if any."""
    label = _label(page, "Name")
    if rs.is_interest(page):
        topic = rs.text_of(page, "Name")
        row = next((r for t, r in interests.items() if t.casefold() == topic.casefold()), None)
        if row and row["notion_page_id"] in pages:
            return "refused", f"Sources: interest {label} is already a row; not added twice"
        topic = tartt.add_interest(topic)["topic"]
        if row:  # mapped to a deleted page: forget it, so the topic maps to this one
            with connection() as conn:
                conn.execute("DELETE FROM notion_sync WHERE entity_table = %s "
                             "AND entity_id = %s", (rs.INTERESTS_TABLE, row["entity_id"]))
        props = {**rs.interest_facts(topic), **rs.owned_props({"active": True},
                                                              rs.INTEREST_OWNED)}
        client.update_page(page["id"], props)
        with connection() as conn:
            _record(conn, rs.INTERESTS_TABLE, topic, page["id"],
                    content_hash(rs.interest_facts(topic)), {"active": True}, True)
        return "interest", None
    args, why = rs.new_feed(page)
    if args is None:
        return "refused", f"Sources: {why}; not added"
    kw = {k: v for k, v in args.items() if v is not None}
    tartt.add_feed(**kw)
    with connection() as conn, conn.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT id, name, url, source_kind, poll_interval_hours, active "
                    "FROM sources WHERE url = %s ORDER BY active DESC, id LIMIT 1",
                    (args["url"],))
        source = cur.fetchone()
    if source is None:
        return "refused", f"Sources: feed {label} wasn't added"
    other = next((f["notion_page_id"] for f in feeds if f["id"] == source["id"]), None)
    if other and other in pages:
        return "refused", (f"Sources: feed {label} has the same URL as row "
                           f"{source['name']!r}; not added twice")
    pv, facts = rs.feed_pg_values(source), rs.feed_facts(source)
    client.update_page(page["id"], {**facts, **rs.owned_props(pv, rs.FEED_OWNED)})
    with connection() as conn:
        _record(conn, rs.SOURCES_TABLE, source["id"], page["id"], content_hash(facts),
                pv, True)
    return "feed", None


def _sources(client: Any, connection: Any, allowed: frozenset[str], tartt: Any,
             notices: list[str]) -> str:
    source, rows = _open(client, connection, rs.SOURCES_KEY, "Sources",
                         rs.SOURCES_COLUMNS, rs.SOURCES_OPTIONS)
    pages = {p["id"]: p for p in rows}
    parent = {"type": "data_source_id", "data_source_id": source}
    counts = {"created": 0, "updated": 0, "pulled": 0, "refused": 0, "feeds": 0,
              "interests": 0, "removed": 0}

    # Hand-added rows first, so a row naming a feed or interest that has no row yet
    # becomes that row instead of the push adding a second one.
    feeds, interests = _feeds(connection), _mapped(connection, rs.INTERESTS_TABLE)
    mapped_pages = ({f["notion_page_id"] for f in feeds if f["notion_page_id"]}
                    | {r["notion_page_id"] for r in interests.values()})
    fresh = new_rows(pages, mapped_pages)
    for page in fresh:
        if operator.foreign_edit(page, allowed):
            notices.append(f"Sources: new row {_label(page, 'Name')} {FOREIGN}")
            counts["refused"] += 1
            continue
        done, notice = _import_row(client, connection, tartt, page, feeds, interests, pages)
        if notice:
            notices.append(notice)
        if done == "refused":
            counts["refused"] += 1
        else:
            counts["feeds" if done == "feed" else "interests"] += 1
    if fresh:
        feeds, interests = _feeds(connection), _mapped(connection, rs.INTERESTS_TABLE)

    for step in plan_feeds(feeds, pages, allowed):
        notices += [f"Sources: feed {step.name!r}: {r}" for r in step.refused]
        counts["refused"] += len(step.refused)
        if step.pulls:
            with connection() as conn, conn.transaction():
                for name, value in step.pulls.items():
                    conn.execute(_FEED_PULL_SQL[name], (value, step.source_id))
            counts["pulled"] += len(step.pulls)
        page_id = step.page_id
        if step.write == "create":
            page_id = client.create_page(parent, step.props)["id"]
            counts["created"] += 1
        elif step.write == "update":
            client.update_page(page_id, step.props)
            counts["updated"] += 1
        with connection() as conn:
            _record(conn, rs.SOURCES_TABLE, step.source_id, page_id, step.digest,
                    step.agreed, step.write != "skip")

    for istep in plan_interests(tartt.list_interests(), interests, pages, allowed):
        notices += [f"Sources: interest {istep.topic!r}: {r}" for r in istep.refused]
        counts["refused"] += len(istep.refused)
        if istep.write == "forget":
            with connection() as conn:
                conn.execute("DELETE FROM notion_sync WHERE entity_table = %s "
                             "AND entity_id = %s", (rs.INTERESTS_TABLE, istep.topic))
            continue
        if istep.pull is True:
            tartt.add_interest(istep.topic)
            counts["interests"] += 1
        elif istep.pull is False:
            tartt.remove_interest(istep.topic)
            counts["removed"] += 1
        page_id = istep.page_id
        if istep.write == "create":
            page_id = client.create_page(parent, istep.props)["id"]
            counts["created"] += 1
        elif istep.write == "update":
            client.update_page(page_id, istep.props)
            counts["updated"] += 1
        with connection() as conn:
            _record(conn, rs.INTERESTS_TABLE, istep.topic, page_id, istep.digest,
                    istep.agreed, istep.write != "skip")
    return (f"Sources {counts['created']} row(s) created, {counts['updated']} updated, "
            f"{counts['pulled']} feed field(s) taken, {counts['feeds']} feed(s) and "
            f"{counts['interests']} interest(s) added, {counts['removed']} interest(s) "
            f"removed, {counts['refused']} refused")


class _Tartt:
    """`tartt_control`, with cognee configured before the first interest call."""

    def __getattr__(self, name: str) -> Any:
        from agents._lib import tartt_control

        if name.endswith("interest") or name.endswith("interests"):
            _configure_cognee()
        return getattr(tartt_control, name)


# --- the run -------------------------------------------------------------------------


def run(client: Any, *, connection: Any, allowed: frozenset[str], today: date,
        ingest: Any = None, tartt: Any = None) -> tuple[str, list[str]]:
    """Take today's Outcomes, Capture, and Sources inputs. Returns a one-line
    summary and the notices (refusals and failures) for the daily briefing.

    `connection` is `db.connection`. `allowed` is `operator.allowed_editors`.
    `ingest` and `tartt` default to the real cognee ingest and `tartt_control`;
    tests pass fakes. A database that fails is reported and the others still run.
    """
    notices: list[str] = []
    parts = []
    sections = (
        ("Outcomes", lambda: _outcomes(client, connection, allowed, today, notices)),
        ("Capture", lambda: _capture(client, connection, allowed, ingest or default_ingest,
                                     notices)),
        ("Sources", lambda: _sources(client, connection, allowed, tartt or _Tartt(), notices)),
    )
    for label, section in sections:
        try:
            parts.append(section())
        except (RuntimeError, NotionError) as e:
            notices.append(f"{label}: not synced: {e}")
            parts.append(f"{label} not synced")
    return "OK    " + "; ".join(parts), notices

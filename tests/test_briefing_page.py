"""The Briefing page, system notes, and urgent alerts (`PRD-notion-integration.md` N9 I6).

Pure and fake-client tests always run. The database tests need Postgres: set
PLAN_TEST_DSN (for example "dbname=postgres"); they use temp tables only.
"""

from __future__ import annotations

import json
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

import pytest

from agents._lib import notion_client as nc
from agents.briefing import run as briefing_run
from agents.notion import briefing_page as bp

DAY = date(2026, 11, 12)
TZ = timezone(timedelta(hours=-5))
NOW = datetime(2026, 11, 12, 6, 0, tzinfo=TZ)


# --- pure --------------------------------------------------------------------------------


def test_title_and_properties():
    assert bp.title(DAY) == "Thu Nov 12"
    assert bp.title(date(2026, 12, 1)) == "Tue Dec 1"
    props = bp.page_properties(DAY)
    assert props["Name"]["title"][0]["text"]["content"] == "Thu Nov 12"
    assert props["Date"] == {"date": {"start": "2026-11-12"}}


def test_schema_problems():
    assert bp.schema_problems({"Name": {"type": "title"}, "Date": {"type": "date"}}) == []
    assert bp.schema_problems({"Name": {"type": "title"}, "Date": {"type": "rich_text"}}) == [
        "column 'Date' (date) is rich_text"]
    assert bp.schema_problems({}) == ["column 'Name' (title) is missing",
                                      "column 'Date' (date) is missing"]


def test_section_blocks_split_paragraphs_and_long_text():
    blocks = bp.section_blocks("Daily plan", "line 1\nline 2\n\n" + "x" * 4500 + "\n\n  ")
    assert blocks[0] == {"type": "heading_3",
                         "heading_3": {"rich_text": nc.rich_text("Daily plan")}}
    assert [b["type"] for b in blocks] == ["heading_3", "paragraph", "paragraph"]
    assert blocks[1]["paragraph"]["rich_text"][0]["text"]["content"] == "line 1\nline 2"
    assert [len(r["text"]["content"]) for r in blocks[2]["paragraph"]["rich_text"]] == [
        2000, 2000, 500]


def test_mention_rich_text():
    assert nc.mention_user("u-1") == {"type": "mention",
                                      "mention": {"type": "user", "user": {"id": "u-1"}}}
    rt = bp.mention_text("u-1", "loop failed")
    assert rt[0]["mention"]["user"]["id"] == "u-1"
    assert rt[1] == {"type": "text", "text": {"content": " loop failed"}}


def test_create_comment_posts_parent_and_rich_text():
    seen = []

    class C(nc.NotionClient):
        def request(self, method, path, body=None):
            seen.append((method, path, body))
            return {"id": "c-1"}

    rt = [nc.mention_user("u-1")]
    assert C("t").create_comment("page-1", rt) == {"id": "c-1"}
    assert seen == [("POST", "/comments", {"parent": {"page_id": "page-1"}, "rich_text": rt})]


def test_recent_notes_and_callout_text():
    notes = [{"at": (NOW - timedelta(hours=h)).isoformat(), "text": f"n{h}"}
             for h in (30, 2, 1)]
    kept = bp.recent_notes(notes, NOW)
    assert [n["text"] for n in kept] == ["n2", "n1"]
    text = bp.system_callout_text(kept)
    assert text.splitlines() == ["System: 2 warning(s) in the last 24 hours",
                                 "• 05:00 n1", "• 04:00 n2"]
    assert bp.system_callout_text([]) == "System: no warnings in the last 24 hours."
    many = [{"at": (NOW - timedelta(minutes=m)).isoformat(), "text": str(m)} for m in range(15)]
    assert len(bp.recent_notes(many, NOW)) == bp.SYSTEM_SHOWN


def _rules(loops=(), spend=()):
    return bp.urgent_rules(loops, spend, global_ceiling=25.0,
                           group_ceilings={"outreach": 20.0})


def test_urgent_rule_two_failures_in_a_row():
    assert _rules([("notion-sync", "todo_sync", ["failed", "failed", "success"], "boom")]) == [
        "notion-sync todo_sync failed 2 runs in a row: boom"]
    assert _rules([("notion-sync", "todo_sync", ["failed", "success", "failed"], None)]) == []
    assert _rules([("notion-sync", "todo_sync", ["failed"], None)]) == []
    assert _rules([("a", "b", ["success", "failed", "failed"], None)]) == []


def test_urgent_rule_spend_ceilings():
    out = _rules(spend=[(date(2026, 11, 11), 25.0, {"outreach": 20.5}),
                        (DAY, 3.0, {"outreach": 0.0})])
    assert out == [
        "The system spend ceiling was hit on Wed Nov 11: $25.00 of $25.00; "
        "later LLM calls were refused",
        "The outreach spend ceiling was hit on Wed Nov 11: $20.50 of $20.00"]
    assert _rules(spend=[(DAY, 24.99, {"outreach": 19.99})]) == []


# --- fake client, fake notion_sync ---------------------------------------------------------


class FakeNotion:
    def __init__(self, existing=None):
        self.calls = []
        self.existing = existing or []
        self.n = 0

    def _id(self, prefix):
        self.n += 1
        return f"{prefix}-{self.n}"

    def retrieve_database(self, database_id):
        assert database_id == "briefings-db"
        return {"data_sources": [{"id": "brief-ds"}]}

    def retrieve_data_source(self, _):
        return {"properties": {"Name": {"type": "title"}, "Date": {"type": "date"}}}

    def iter_query(self, source, filter=None):
        self.calls.append(("query", source, filter))
        return iter(self.existing)

    def create_page(self, parent, properties):
        self.calls.append(("create_page", parent, properties))
        return {"id": self._id("page")}

    def append_blocks(self, block_id, children, after=None):
        self.calls.append(("append", block_id, [c["type"] for c in children], after))
        return [{"id": self._id("blk")} for _ in children]

    def update_block(self, block_id, body):
        self.calls.append(("update_block", block_id, body))
        return {}

    def create_comment(self, page_id, rich_text):
        self.calls.append(("comment", page_id, rich_text))
        return {"id": self._id("cmt")}


@pytest.fixture
def sync(monkeypatch):
    rows = {("notion_schema", "briefings_database"): "briefings-db",
            ("notion_schema", "operator_user"): "op-user"}
    monkeypatch.setattr(bp, "_lookup", lambda _c, t, k: rows.get((t, k)))
    monkeypatch.setattr(bp, "_record", lambda _c, t, k, v: rows.__setitem__((t, k), v))

    @contextmanager
    def connection():
        yield None
    monkeypatch.setattr(bp, "schema_id", lambda _c, key: rows[("notion_schema", key)])
    return rows, connection


def test_today_page_creates_once_then_reuses(sync):
    rows, connection = sync
    client = FakeNotion()
    page = bp.today_page(client, connection, DAY)
    assert page == "page-1" and rows[("briefing", "2026-11-12")] == "page-1"
    assert client.calls[0] == ("query", "brief-ds",
                               {"property": "Date", "date": {"equals": "2026-11-12"}})
    assert client.calls[1][0] == "create_page"
    assert client.calls[1][1] == {"type": "data_source_id", "data_source_id": "brief-ds"}
    assert bp.today_page(client, connection, DAY) == "page-1"
    assert len(client.calls) == 2


def test_today_page_adopts_an_existing_row(sync):
    rows, connection = sync
    client = FakeNotion(existing=[{"id": "hand-made"}])
    assert bp.today_page(client, connection, DAY) == "hand-made"
    assert [c[0] for c in client.calls] == ["query"]


def test_today_page_refuses_a_bad_schema(sync):
    _, connection = sync
    client = FakeNotion()
    client.retrieve_data_source = lambda _: {"properties": {"Name": {"type": "title"}}}
    with pytest.raises(RuntimeError, match="'Date' \\(date\\) is missing"):
        bp.today_page(client, connection, DAY)


def test_append_writes_a_section_and_once_keys_skip_reruns(sync):
    rows, connection = sync
    client = FakeNotion()
    bp.append(client, connection, DAY, "Daily plan", "a\n\nb", key="plan")
    assert client.calls[-1] == ("append", "page-1", ["heading_3", "paragraph", "paragraph"],
                                None)
    before = len(client.calls)
    bp.append(client, connection, DAY, "Daily plan", "a\n\nb", key="plan")
    assert len(client.calls) == before
    bp.append(client, connection, DAY, "Leads", "c")  # no key: always appends
    assert client.calls[-1][2] == ["heading_3", "paragraph"]


def test_append_chunks_over_100_children(sync):
    _, connection = sync
    client = FakeNotion()
    bp.append(client, connection, DAY, "Many", "\n\n".join(str(i) for i in range(150)))
    appends = [c for c in client.calls if c[0] == "append"]
    assert [len(c[2]) for c in appends] == [100, 51]


def test_system_note_urgent_without_dashboard(sync, monkeypatch):
    rows, connection = sync
    monkeypatch.setattr(bp, "refresh_system_callout", lambda *a, **k: "skipped")
    client = FakeNotion()
    bp.system_note(client, connection, DAY, "todo_sync failed twice", urgent=True, now=NOW)
    kinds = [c[0] for c in client.calls]
    assert kinds == ["query", "create_page", "append", "append", "comment"]
    assert client.calls[2][2:] == (["heading_3"], None)  # the System heading, at the end
    assert client.calls[3][3] == "blk-2"  # the note goes right after it
    comment = client.calls[4]
    assert comment[1] == "page-1" and comment[2][0]["mention"]["user"]["id"] == "op-user"
    # The next note follows the last one; the same urgent text isn't commented twice.
    bp.system_note(client, connection, DAY, "todo_sync failed twice", urgent=True, now=NOW)
    assert client.calls[-1][0] == "append" and client.calls[-1][3] == "blk-3"
    assert sum(1 for c in client.calls if c[0] == "comment") == 1


def test_surface_off_reads_config(monkeypatch):
    from agents.discord_bot import config

    monkeypatch.setattr(config, "DISCORD_SURFACES_OFF", frozenset({"briefing"}), raising=False)
    assert bp.surface_off("briefing") and not bp.surface_off("tasks_posts")
    monkeypatch.delattr(config, "DISCORD_SURFACES_OFF")
    assert not bp.surface_off("briefing")


# --- the briefing's Notion sections ----------------------------------------------------------


def test_briefing_notion_sections():
    status = {"notes_total": 1, "notes_24h": 0, "spend_24h": 0.0, "calls_24h": 2,
              "failures_24h": 0, "outcomes_total": 3}
    recs = briefing_run.format_reading_recs(
        [{"url": "https://ex.com/a", "title": "A", "interest_score": 0.5}])
    out = briefing_run.notion_sections(datetime(2026, 11, 12, 6), status, pending=4,
                                       recs_text=recs, prospects_text="", outreach_line="",
                                       spend_text="💵 **Spend: $1**\n• Outreach $0")
    assert [h for h, _ in out] == ["System status", "Task Tinder", "Reading", "Spend"]
    assert out[0][1].startswith("• Notes captured: 1 total")
    assert out[1][1] == "4 candidate(s) pending"
    assert out[2][1] == "• [A](https://ex.com/a) — interest 0.50"
    assert out[3][1] == "💵 Spend: $1\n• Outreach $0"
    assert all("**" not in t for _, t in out)


# --- database ------------------------------------------------------------------------------

DSN = os.environ.get("PLAN_TEST_DSN")
db_only = pytest.mark.skipif(not DSN, reason="PLAN_TEST_DSN not set")

_TABLES = """
CREATE TEMP TABLE agent_runs (id bigserial PRIMARY KEY, agent_name text NOT NULL,
    function_label text NOT NULL, trigger_kind text, started_at timestamptz NOT NULL,
    ended_at timestamptz, status text NOT NULL, usd_cost numeric(14,8), error_text text);
CREATE TEMP TABLE notion_sync (entity_table text, entity_id text, notion_page_id text UNIQUE,
    last_pushed_at timestamptz, synced_fields jsonb, PRIMARY KEY (entity_table, entity_id));
INSERT INTO notion_sync (entity_table, entity_id, notion_page_id) VALUES
    ('notion_schema', 'briefings_database', 'briefings-db'),
    ('notion_schema', 'operator_user', 'op-user'),
    ('dashboard_block', '2026-Q4:page', 'dash-page'),
    ('dashboard_block', '2026-Q4:attention', 'att-1');
"""


@pytest.fixture
def conn():
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET search_path = pg_temp")
        c.execute(_TABLES)

        @contextmanager
        def same():
            yield c
        c.same = same
        yield c


def _runs(conn, rows):
    for agent, label, hours_ago, status, usd, err in rows:
        conn.execute("INSERT INTO agent_runs (agent_name, function_label, started_at, status, "
                     "usd_cost, error_text) VALUES (%s, %s, %s, %s, %s, %s)",
                     (agent, label, NOW - timedelta(hours=hours_ago), status, usd, err))


@db_only
def test_urgent_reasons_from_agent_runs(conn):
    _runs(conn, [
        ("notion-sync", "todo_sync", 1, "failed", 0, "NotionError: 502"),
        ("notion-sync", "todo_sync", 2, "failed", 0, "older"),
        ("notion-sync", "todo_sync", 3, "success", 0, None),
        ("notion-sync", "okr_pull", 1, "success", 0, None),  # recovered
        ("notion-sync", "okr_pull", 2, "failed", 0, None),
        ("notion-sync", "okr_pull", 3, "failed", 0, None),
        ("tartt", "fetch", 100, "failed", 0, None),  # stale: not alerted
        ("tartt", "fetch", 101, "failed", 0, None),
        ("outreach-sourcing", "run", 20, "success", 21, None),  # yesterday, local
        ("tartt", "score", 22, "success", 4.5, None),
    ])
    reasons = bp.urgent_reasons(conn, NOW)
    assert reasons == [
        "notion-sync todo_sync failed 2 runs in a row: NotionError: 502",
        "The system spend ceiling was hit on Wed Nov 11: $25.50 of $25.00; "
        "later LLM calls were refused",
        "The outreach spend ceiling was hit on Wed Nov 11: $21.00 of $20.00"]


@db_only
def test_system_note_builds_and_refreshes_the_dashboard_callout(conn):
    client = FakeNotion()
    bp.system_note(client, conn.same, DAY, "okr_pull warned", now=NOW)
    callout = [c for c in client.calls if c[0] == "append" and c[2] == ["callout"]]
    assert callout == [("append", "dash-page", ["callout"], "att-1")]
    assert not [c for c in client.calls if c[0] == "comment"]
    bp.system_note(client, conn.same, DAY, "sourcing short", now=NOW + timedelta(hours=1))
    update = client.calls[-1]
    assert update[0] == "update_block" and update[1] == callout_id(conn)
    text = update[2]["callout"]["rich_text"][0]["text"]["content"]
    assert text.splitlines()[1:] == ["• 07:00 sourcing short", "• 06:00 okr_pull warned"]
    stored = conn.execute("SELECT synced_fields FROM notion_sync WHERE entity_id = "
                          "'2026-Q4:system'").fetchone()[0]
    stored = json.loads(stored) if isinstance(stored, str) else stored
    assert [n["text"] for n in stored["notes"]] == ["okr_pull warned", "sourcing short"]
    # A day later both notes have aged out.
    bp.refresh_system_callout(client, conn.same, NOW + timedelta(hours=30))
    assert client.calls[-1][2]["callout"]["rich_text"][0]["text"]["content"] == (
        "System: no warnings in the last 24 hours.")


def callout_id(conn):
    return conn.execute("SELECT notion_page_id FROM notion_sync WHERE entity_id = "
                        "'2026-Q4:system'").fetchone()[0]

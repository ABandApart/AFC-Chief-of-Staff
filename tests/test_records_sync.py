"""Tests for the Notion Outcomes, Capture, and Sources inputs (PRD-notion-integration.md N9 I5).

The pure tests always run. The database test needs Postgres: set PLAN_TEST_DSN
(for example `dbname=postgres`). It builds the tables it touches as temporary
tables, so no real table is read or written, and is skipped when unset. Notion
is a fake client throughout; nothing calls the live API.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date
from typing import Any

import pytest

from agents._lib.notion_client import content_hash
from agents.notion import records_schema as rs
from agents.notion import records_sync as sync

TODAY = date(2026, 11, 16)
OPERATOR, COS, STRANGER = "user-op", "user-cos", "user-other"
ALLOWED = frozenset({OPERATOR, COS})


# --- fixtures ------------------------------------------------------------------------


def prop(kind: str, value: Any) -> dict[str, Any]:
    if kind in ("title", "rich_text"):
        return {"type": kind, kind: [{"plain_text": value}] if value else []}
    if kind == "select":
        return {"type": kind, "select": {"name": value} if value else None}
    if kind == "date":
        return {"type": kind, "date": {"start": value} if value else None}
    return {"type": kind, kind: value}


def page(pid: str, columns: dict[str, str], editor: str | None = OPERATOR,
         **values: Any) -> dict[str, Any]:
    """A Notion row. Keyword names use underscores for spaces (`CoS_id`)."""
    vals = {k.replace("_", " "): v for k, v in values.items()}
    out = {"id": pid, "created_time": "2026-11-14T09:00:00.000Z",
           "properties": {n: prop(k, vals.get(n, False if k == "checkbox" else None))
                          for n, k in columns.items()}}
    if editor:
        out["last_edited_by"] = {"object": "user", "id": editor}
    return out


def schema(columns: dict[str, str], options: dict[str, tuple[str, ...]]) -> dict[str, Any]:
    out: dict[str, Any] = {n: {"type": k} for n, k in columns.items()}
    for n, names in options.items():
        out[n][columns[n]] = {"options": [{"name": o} for o in names]}
    return out


def feed(i=1, **kw):
    base = {"id": i, "name": "Stratechery", "url": "https://stratechery.com/feed",
            "source_kind": "rss", "poll_interval_hours": 12, "active": True,
            "notion_page_id": None, "content_hash": None, "synced_fields": None}
    return {**base, **kw}


def feed_page(pid="p1", editor=OPERATOR, **kw):
    vals = {"Name": "Stratechery", "Kind": "rss", "URL": "https://stratechery.com/feed",
            "Every": 12, "Active": True, "CoS_id": 1, **kw}
    return page(pid, rs.SOURCES_COLUMNS, editor, **vals)


def synced_feed(**kw):
    s = feed(**kw)
    return {**s, "notion_page_id": "p1", "content_hash": content_hash(rs.feed_facts(s)),
            "synced_fields": rs.feed_pg_values(s)}


def one_feed(s, p):
    [step] = sync.plan_feeds([s], {p["id"]: p} if p else {}, ALLOWED)
    return step


# --- schema ------------------------------------------------------------------------


@pytest.mark.parametrize("columns,options", [
    (rs.OUTCOMES_COLUMNS, rs.OUTCOMES_OPTIONS),
    (rs.CAPTURE_COLUMNS, rs.CAPTURE_OPTIONS),
    (rs.SOURCES_COLUMNS, rs.SOURCES_OPTIONS),
])
def test_schema_ready(columns, options):
    assert rs.schema_problems(schema(columns, options), columns, options) == []


def test_schema_problems_name_missing_columns_and_options():
    props = schema(rs.SOURCES_COLUMNS, {"Kind": ("rss", "arxiv")})
    del props["Every"]
    props["URL"] = {"type": "rich_text"}
    assert rs.schema_problems(props, rs.SOURCES_COLUMNS, rs.SOURCES_OPTIONS) == [
        "column 'URL' (url) is rich_text", "column 'Every' (number) is missing",
        "Kind option(s) missing: newsletter, interest"]


def test_outcome_types_are_the_cogs():
    from agents._lib.outcomes import OUTCOME_TYPES

    assert rs.OUTCOMES_OPTIONS["Type"] == tuple(OUTCOME_TYPES)
    assert len(OUTCOME_TYPES) == 8


# --- Outcomes ------------------------------------------------------------------------


def outcome_page(pid="o1", editor=OPERATOR, **kw):
    vals = {"Outcome": "Signed a pilot", "Type": "proposal_sent", **kw}
    return page(pid, rs.OUTCOMES_COLUMNS, editor, **vals)


def test_outcome_fields_read_the_row():
    p = outcome_page(Value=18000, Company="acme.com", Date="2026-11-10T00:00:00.000Z")
    assert rs.outcome_fields(p) == {"description": "Signed a pilot", "type": "proposal_sent",
                                    "value": 18000, "company": "acme.com",
                                    "date": "2026-11-10"}
    assert rs.outcome_date(rs.outcome_fields(p)) == date(2026, 11, 10)


@pytest.mark.parametrize("kw,why", [
    ({}, None),
    ({"Type": None}, "Type is blank"),
    ({"Type": "won_lottery"}, "Type 'won_lottery' is not an outcome type"),
    ({"Value": -5}, "Value -5 is negative"),
    ({"Type": "engagement_signed"}, "engagement_signed needs a Company"),
    ({"Type": "engagement_signed", "Company": "acme.com"}, None),
])
def test_outcome_refusal(kw, why):
    got = rs.outcome_refusal(rs.outcome_fields(outcome_page(**kw)))
    assert (got is None) if why is None else got.startswith(why)


def test_contract_outcome_without_a_matching_company_is_refused():
    assert rs.company_refusal("engagement_signed", "no client matches 'Acme'") == (
        "engagement_signed needs a matching company: no client matches 'Acme'")
    assert rs.company_refusal("proposal_sent", "no client matches 'Acme'") is None
    assert rs.company_refusal("engagement_signed", None) is None


def test_outcome_written_is_only_the_cos_id():
    assert rs.outcome_written(7) == {"CoS id": {"number": 7}}


# --- Capture -------------------------------------------------------------------------


def test_capture_text_and_pending():
    p = page("c1", rs.CAPTURE_COLUMNS, Note="Call with Dana", Body="She wants a pilot.")
    assert rs.capture_text(p) == "Call with Dana\n\nShe wants a pilot."
    assert rs.capture_pending(p)
    assert not rs.capture_pending(page("c2", rs.CAPTURE_COLUMNS))  # empty row
    assert not rs.capture_pending(page("c3", rs.CAPTURE_COLUMNS, Note="x", Ingested=True))


def test_capture_written():
    out = rs.capture_written(ingested=True, result="Captured to memory.")
    assert out["Ingested"] == {"checkbox": True}
    assert out["Result"]["rich_text"][0]["text"]["content"] == "Captured to memory."


# --- Sources: projection and merge ---------------------------------------------------


def test_feed_projection():
    s = feed()
    assert rs.feed_facts(s) == {"Name": {"title": [{"type": "text",
                                                    "text": {"content": "Stratechery"}}]},
                                "Kind": {"select": {"name": "rss"}}, "CoS id": {"number": 1}}
    assert rs.owned_props(rs.feed_pg_values(s), rs.FEED_OWNED) == {
        "Active": {"checkbox": True}, "Every": {"number": 12},
        "URL": {"url": "https://stratechery.com/feed"}}
    assert rs.feed_notion_values(feed_page()) == rs.feed_pg_values(s)


def test_new_feed_creates_a_full_row():
    step = one_feed(feed(), None)
    assert step.write == "create"
    assert step.props["Every"] == {"number": 12} and step.props["CoS id"] == {"number": 1}


def test_unchanged_feed_is_skipped():
    step = one_feed(synced_feed(), feed_page())
    assert (step.write, step.props, step.pulls) == ("skip", {}, {})


def test_notion_change_wins():
    step = one_feed(synced_feed(), feed_page(Active=False, Every=24))
    assert step.pulls == {"active": False, "every": 24}
    assert step.agreed == {"active": False, "every": 24, "url": "https://stratechery.com/feed"}
    assert step.write == "skip"


def test_postgres_change_is_pushed_when_notion_did_not_change():
    s = {**synced_feed(), "active": False}  # paused from Discord
    step = one_feed(s, feed_page())
    assert step.pulls == {} and step.props == {"Active": {"checkbox": False}}


def test_foreign_editor_is_refused_and_restored():
    step = one_feed(synced_feed(), feed_page(editor=STRANGER, Active=False))
    assert step.pulls == {}
    assert step.refused == [f"Active {sync.FOREIGN}"]
    assert step.props == {"Active": {"checkbox": True}}


def test_bad_every_is_refused():
    step = one_feed(synced_feed(), feed_page(Every=1.5))
    assert step.pulls == {} and step.props == {"Every": {"number": 12}}
    assert "whole number" in step.refused[0]


def test_new_feed_row_needs_a_url():
    args, why = rs.new_feed(page("n1", rs.SOURCES_COLUMNS, Name="TechCrunch"))
    assert args is None and why == "feed 'TechCrunch' needs a URL"
    args, why = rs.new_feed(page("n1", rs.SOURCES_COLUMNS, Name="TC", URL="https://tc.com/f",
                                 Every=6))
    assert why is None and args == {"name": "TC", "url": "https://tc.com/f",
                                    "source_kind": "rss", "cadence_hours": 6}


def interest_page(pid, topic, active=True, editor=OPERATOR):
    return page(pid, rs.SOURCES_COLUMNS, editor, Name=topic, Kind="interest", Active=active)


def mapped(topic, pid, active=True):
    return {topic: {"entity_id": topic, "notion_page_id": pid, "synced_fields":
                    {"active": active},
                    "content_hash": content_hash(rs.interest_facts(topic))}}


def test_new_interest_gets_a_row():
    [step] = sync.plan_interests(["AI policy"], {}, {}, ALLOWED)
    assert step.write == "create" and step.props["Kind"] == {"select": {"name": "interest"}}
    assert "CoS id" not in step.props


def test_unticking_an_interest_removes_it():
    [step] = sync.plan_interests(["AI policy"], mapped("AI policy", "i1"),
                                 {"i1": interest_page("i1", "AI policy", active=False)},
                                 ALLOWED)
    assert step.pull is False and step.write == "skip"


def test_untick_by_a_stranger_is_refused():
    [step] = sync.plan_interests(["AI policy"], mapped("AI policy", "i1"),
                                 {"i1": interest_page("i1", "AI policy", False, STRANGER)},
                                 ALLOWED)
    assert step.pull is None and step.props == {"Active": {"checkbox": True}}


def test_interest_removed_elsewhere_unticks_the_row():
    [step] = sync.plan_interests([], mapped("AI policy", "i1"),
                                 {"i1": interest_page("i1", "AI policy")}, ALLOWED)
    assert step.pull is None and step.props == {"Active": {"checkbox": False}}


def test_inactive_interest_whose_row_was_deleted_is_forgotten():
    [step] = sync.plan_interests([], mapped("AI policy", "gone", active=False), {}, ALLOWED)
    assert step.write == "forget"


def test_new_rows_skip_mapped_and_numbered_rows():
    pages = {"a": feed_page("a"), "b": interest_page("b", "x"),
             "c": page("c", rs.SOURCES_COLUMNS, Name="New"), "d": page("d", rs.SOURCES_COLUMNS)}
    assert [p["id"] for p in sync.new_rows(pages, {"b"})] == ["c"]


# --- the run, with a fake Notion ----------------------------------------------------


class FakeNotion:
    """Three databases, keyed `<key>` → data source `ds-<key>`."""

    def __init__(self):
        self.rows: dict[str, list[dict[str, Any]]] = {}
        self.schemas = {"ds-outcomes": schema(rs.OUTCOMES_COLUMNS, rs.OUTCOMES_OPTIONS),
                        "ds-capture": schema(rs.CAPTURE_COLUMNS, rs.CAPTURE_OPTIONS),
                        "ds-sources": schema(rs.SOURCES_COLUMNS, rs.SOURCES_OPTIONS)}
        for ds in self.schemas:
            self.rows[ds] = []
        self.updates: list[tuple[str, dict]] = []
        self.creates: list[dict] = []

    def retrieve_database(self, database_id):
        return {"data_sources": [{"id": f"ds-{database_id}"}]}

    def retrieve_data_source(self, ds):
        return {"properties": self.schemas[ds]}

    def iter_query(self, ds):
        return list(self.rows[ds])

    def _find(self, pid):
        return next(p for rows in self.rows.values() for p in rows if p["id"] == pid)

    def update_page(self, pid, props):
        self.updates.append((pid, props))
        p = self._find(pid)
        p["properties"].update(props)
        p["last_edited_by"] = {"object": "user", "id": COS}
        return p

    def create_page(self, parent, props):
        pid = f"new-{len(self.creates) + 1}"
        self.creates.append(props)
        self.rows[parent["data_source_id"]].append(
            {"id": pid, "properties": dict(props),
             "last_edited_by": {"object": "user", "id": COS}})
        return {"id": pid}


def test_a_database_not_set_up_is_reported_and_the_others_run():
    @contextmanager
    def conn():
        class C:
            def execute(self, *a):
                class R:
                    def fetchone(self):
                        return None
                return R()
        yield C()

    summary, notices = sync.run(FakeNotion(), connection=conn, allowed=ALLOWED, today=TODAY,
                                ingest=lambda notes: [], tartt=object())
    assert summary.startswith("OK    Outcomes not synced; Capture not synced")
    assert len(notices) == 3 and "outcomes_database" in notices[0]


# --- database ------------------------------------------------------------------------

DSN = os.environ.get("PLAN_TEST_DSN")
db_only = pytest.mark.skipif(not DSN, reason="PLAN_TEST_DSN not set")

_TABLES = """
CREATE TEMP TABLE notion_sync (id bigserial PRIMARY KEY, entity_table text NOT NULL,
    entity_id text NOT NULL, notion_page_id text NOT NULL UNIQUE, content_hash text,
    synced_done boolean, synced_fields jsonb, last_pushed_at timestamptz,
    last_pulled_at timestamptz, UNIQUE (entity_table, entity_id));
CREATE TEMP TABLE clients (id bigserial PRIMARY KEY, name text NOT NULL, domain text UNIQUE);
CREATE TEMP TABLE outreach_targets (id bigserial PRIMARY KEY, company_name text,
    company_domain text);
CREATE TEMP TABLE outcomes (id bigserial PRIMARY KEY, outcome_type text NOT NULL,
    outcome_value numeric, description text NOT NULL,
    recorded_at timestamptz NOT NULL DEFAULT now(), client_id bigint REFERENCES clients(id),
    attributed_target_id bigint,
    CONSTRAINT outcomes_engagement_has_client_ck CHECK (
        outcome_type NOT IN ('engagement_signed', 'engagement_renewed', 'maintenance_converted')
        OR client_id IS NOT NULL));
CREATE TEMP TABLE sources (id bigserial PRIMARY KEY, name text NOT NULL, url text NOT NULL,
    source_kind text NOT NULL, trust_score real NOT NULL DEFAULT 0.5,
    poll_interval_hours integer NOT NULL DEFAULT 24, active boolean NOT NULL DEFAULT true);
INSERT INTO notion_sync (entity_table, entity_id, notion_page_id) VALUES
    ('notion_schema', 'outcomes_database', 'outcomes'),
    ('notion_schema', 'capture_database', 'capture'),
    ('notion_schema', 'sources_database', 'sources');
INSERT INTO outreach_targets (company_name, company_domain) VALUES ('Acme Learning', 'acme.com');
INSERT INTO sources (name, url, source_kind, poll_interval_hours)
    VALUES ('Stratechery', 'https://stratechery.com/feed', 'rss', 12);
"""


class FakeTartt:
    """Interests in memory; feeds through the real `tartt_control.add_feed`."""

    def __init__(self, topics):
        self.topics = list(topics)

    def add_feed(self, **kw):
        from agents._lib import tartt_control

        return tartt_control.add_feed(**kw)

    def list_interests(self):
        return sorted(self.topics)

    def add_interest(self, topic):
        if topic.casefold() not in {t.casefold() for t in self.topics}:
            self.topics.append(topic)
        return {"status": "added", "topic": topic}

    def remove_interest(self, topic):
        self.topics = [t for t in self.topics if t.casefold() != topic.casefold()]
        return {"status": "removed", "topic": topic}


@db_only
def test_run_end_to_end(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from agents._lib import tartt_control

    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET search_path = pg_temp")
        c.execute(_TABLES)

        @contextmanager
        def same():
            yield c
        monkeypatch.setattr(tartt_control.db, "connection", same)

        notion = FakeNotion()
        notion.rows["ds-outcomes"] = [
            outcome_page("o1", Type="engagement_signed", Company="acme.com", Value=18000,
                         Date="2026-11-10"),
            outcome_page("o2", Type="engagement_signed", Company="Nobody Inc"),
            outcome_page("o3", editor=STRANGER),
            outcome_page("o4", CoS_id=99),
            outcome_page("o5", Outcome=None),
        ]
        notion.rows["ds-capture"] = [
            page("c1", rs.CAPTURE_COLUMNS, Note="Call with Dana", Body="Wants a pilot."),
            page("c2", rs.CAPTURE_COLUMNS, Note="boom"),
            page("c3", rs.CAPTURE_COLUMNS, STRANGER, Note="from someone else"),
        ]
        notion.rows["ds-sources"] = [
            page("s-new", rs.SOURCES_COLUMNS, Name="TechCrunch", URL="https://tc.com/feed",
                 Every=6),
            interest_page("s-int", "energy transition"),
        ]
        ingested: list[tuple[str, str]] = []

        def ingest(notes):
            ingested.extend(notes)
            return [RuntimeError("cognify failed") if "boom" in t else "captured"
                    for t, _ in notes]

        tartt = FakeTartt(["AI policy"])
        kw = {"connection": same, "allowed": ALLOWED, "today": TODAY, "ingest": ingest,
              "tartt": tartt}
        summary, notices = sync.run(notion, **kw)

        # Outcomes: one imported, with client and target; the rest refused or skipped.
        rows = c.execute("SELECT o.id, outcome_type, outcome_value, c.domain, "
                         "attributed_target_id, recorded_at::date FROM outcomes o "
                         "JOIN clients c ON c.id = o.client_id").fetchall()
        assert len(rows) == 1
        oid, kind, value, domain, target, on = rows[0]
        assert (kind, value, domain, target, on) == ("engagement_signed", 18000, "acme.com", 1,
                                                     date(2026, 11, 10))
        assert rs.cos_id(notion._find("o1")) == oid
        assert c.execute("SELECT count(*) FROM clients").fetchone()[0] == 1
        assert any("'Nobody Inc'" in n and "needs a matching company" in n for n in notices)
        assert any("Outcomes:" in n and sync.FOREIGN in n for n in notices)
        assert "Outcomes 1 imported, 2 refused" in summary

        # Capture: one ingested, one failed and left for retry, one refused.
        assert [ref for _, ref in ingested] == ["notion:c1", "notion:c2"]
        assert rs.checkbox_of(notion._find("c1"), "Ingested")
        assert not rs.checkbox_of(notion._find("c2"), "Ingested")
        assert "cognify failed" in rs.text_of(notion._find("c2"), "Result")
        assert any("'boom' not ingested" in n for n in notices)
        assert any("Capture: 'from someone else'" in n for n in notices)

        # Sources: the hand-added feed and interest landed; existing ones got rows.
        assert c.execute("SELECT name, poll_interval_hours FROM sources ORDER BY id"
                         ).fetchall() == [("Stratechery", 12), ("TechCrunch", 6)]
        assert rs.cos_id(notion._find("s-new")) == 2
        assert tartt.list_interests() == ["AI policy", "energy transition"]
        names = sorted(rs.text_of(p, "Name") for p in notion.rows["ds-sources"])
        assert names == ["AI policy", "Stratechery", "TechCrunch", "energy transition"]

        # Second run: nothing new to write, nothing imported twice.
        notion.updates.clear()
        notion.creates.clear()
        ingested.clear()
        summary, notices = sync.run(notion, **kw)
        assert c.execute("SELECT count(*) FROM outcomes").fetchone()[0] == 1
        assert [ref for _, ref in ingested] == ["notion:c2"]  # the failure is retried
        assert [pid for pid, _ in notion.updates] == []  # same failure: hash-skipped
        assert notion.creates == []
        assert "Sources 0 row(s) created, 0 updated" in summary

        # The operator pauses a feed and unticks an interest.
        strat = next(p for p in notion.rows["ds-sources"]
                     if rs.text_of(p, "Name") == "Stratechery")
        strat["properties"]["Active"] = prop("checkbox", False)
        strat["last_edited_by"] = {"object": "user", "id": OPERATOR}
        energy = notion._find("s-int")
        energy["properties"]["Active"] = prop("checkbox", False)
        energy["last_edited_by"] = {"object": "user", "id": OPERATOR}
        summary, notices = sync.run(notion, **kw)
        assert c.execute("SELECT active FROM sources WHERE id = 1").fetchone()[0] is False
        assert tartt.list_interests() == ["AI policy"]
        assert "1 feed field(s) taken" in summary and "1 interest(s) removed" in summary

        # A stranger re-ticks the feed: refused, and the CoS writes Active back.
        strat["properties"]["Active"] = prop("checkbox", True)
        strat["last_edited_by"] = {"object": "user", "id": STRANGER}
        summary, notices = sync.run(notion, **kw)
        assert c.execute("SELECT active FROM sources WHERE id = 1").fetchone()[0] is False
        assert not rs.checkbox_of(strat, "Active")
        assert any("feed 'Stratechery'" in n and sync.FOREIGN in n for n in notices)

"""Tests for the daily plan loop (PRD-notion-integration.md N7, verifications 1 and 2).

The pure tests always run. The database tests need Postgres: set PLAN_TEST_DSN
(for example `dbname=postgres`). They build the tables the loop touches as
temporary tables, so no real table is read or written, and are skipped when unset.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date

import pytest

from agents._lib import task_tinder
from agents.notion import plan_daily as pd
from agents.notion import plan_import as pi
from agents.notion import todo_schema as todo
from cli.notion_plan_setup import todo_patch

FRI, SAT, MON = date(2026, 11, 20), date(2026, 11, 21), date(2026, 11, 23)

# --- pure ----------------------------------------------------------------------------------


def test_window_is_the_last_five_plan_days():
    days = [date(2026, 11, d) for d in (16, 17, 18, 19, 20, 23, 24)]
    assert pd.window(days, date(2026, 11, 24)) == (date(2026, 11, 18), date(2026, 11, 24))
    assert pd.window(days[:2], date(2026, 11, 17)) == (date(2026, 11, 16), date(2026, 11, 17))
    assert pd.window([], FRI) == (FRI, FRI)


def test_prior_day_skips_weekends_and_off_days():
    days = [date(2026, 11, 25), date(2026, 11, 27), FRI, MON]
    assert pd.prior_day(days, MON) == FRI
    assert pd.prior_day(days, date(2026, 11, 27)) == date(2026, 11, 25)  # Thanksgiving OFF
    assert pd.prior_day(days, FRI) is None


def test_summary_lines():
    text = pd.summary(MON, pd.Today(9, 285, 3), FRI, pd.Prior(7, 5, 255, 165), 2,
                      "https://www.notion.so/abc")
    assert text.splitlines() == [
        "Fri Nov 20: 5 of 7 done · 165 of 255 min · 2 carried to today",
        "Today, Mon Nov 23: 9 tasks · 285 min (budget 180) · 3 marked 🔻",
        "Dashboard: <https://www.notion.so/abc>",
    ]


def test_summary_first_day_singular_and_no_link():
    assert pd.summary(FRI, pd.Today(1, 30, 0), None, None, 0, None) == \
        "Today, Fri Nov 20: 1 task · 30 min (budget 180)"


def test_view_filter_uses_property_ids():
    f = pd.view_filter({"Due": "d1", "Source": "s1"}, FRI, MON)
    assert f == {"and": [
        {"property": "s1", "select": {"equals": "plan"}},
        {"property": "d1", "date": {"on_or_after": "2026-11-20"}},
        {"property": "d1", "date": {"on_or_before": "2026-11-23"}}]}


def test_plan_task_facts():
    task = {"id": 9, "created_at": None, "source_type": None, "imported": True,
            "planned_for": FRI, "plan_minutes": 30, "plan_context": "why"}
    facts = todo.fact_props(task)
    assert facts["Source"] == {"select": {"name": "plan"}}  # not "notion"
    assert facts["Minutes"] == {"number": 30}
    assert facts["Planned for"] == {"date": {"start": "2026-11-20"}}
    assert facts["Context"]["rich_text"][0]["text"]["content"] == "why"


def test_other_tasks_keep_their_facts():
    facts = todo.fact_props({"id": 1, "created_at": None, "source_type": "operator"})
    assert set(facts) == {"Source", "Created", "CoS id"}


def test_todo_patch_adds_only_what_is_missing():
    props = {"Minutes": {}, "Source": {"select": {"options": [
        {"id": "a", "name": "discovery", "color": "default"}]}}}
    patch = todo_patch(props)
    assert set(patch) == {"Planned for", "Context", "Source"}
    assert [o["name"] for o in patch["Source"]["select"]["options"]] == ["discovery", "plan"]
    done = {**dict.fromkeys(todo.PLAN_PROPERTIES, {}),
            "Source": {"select": {"options": [{"name": "plan"}]}}}
    assert todo_patch(done) == {}


# --- database ------------------------------------------------------------------------------

DSN = os.environ.get("PLAN_TEST_DSN")
db_only = pytest.mark.skipif(not DSN, reason="PLAN_TEST_DSN not set")

_TABLES = """
CREATE TEMP TABLE plans (id bigserial PRIMARY KEY, notion_page_id text UNIQUE, title text,
    starts_on date, ends_on date, status text NOT NULL DEFAULT 'active',
    imported_at timestamptz DEFAULT now());
CREATE TEMP TABLE plan_items (id bigserial PRIMARY KEY, plan_id bigint, day date,
    position smallint, title text, minutes smallint, okr_initiative_id bigint, context text,
    standing boolean NOT NULL DEFAULT false, cut_first boolean NOT NULL DEFAULT false,
    task_id bigint UNIQUE, UNIQUE (plan_id, day, position));
CREATE TEMP TABLE tasks (id bigserial PRIMARY KEY, title text NOT NULL, description text,
    owner text, due_date date, status text, follow_up_id bigint, okr_initiative_id bigint,
    completed_at timestamptz, created_at timestamptz DEFAULT now());
CREATE TEMP TABLE follow_ups (id bigserial PRIMARY KEY, owner text, action text,
    deadline date, status text, escalation_level smallint, completed_at timestamptz);
CREATE TEMP TABLE notion_sync (entity_table text, entity_id text, notion_page_id text,
    last_pushed_at timestamptz, PRIMARY KEY (entity_table, entity_id));
INSERT INTO notion_sync (entity_table, entity_id, notion_page_id) VALUES
    ('notion_schema', 'todo_database', 'todo-db'),
    ('dashboard_block', '2026-Q4:page', 'page-1'),
    ('dashboard_block', '2026-Q4:attention', 'att-1');
"""


@pytest.fixture
def conn(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET search_path = pg_temp")
        c.execute(_TABLES)

        @contextmanager
        def same():
            yield c
        monkeypatch.setattr(task_tinder.db, "connection", same)
        c.same = same
        yield c


def _plan(**days) -> pi.Plan:
    out = pi.Plan("Test plan", FRI, MON)
    for d, items in days.items():
        out.days[{"fri": FRI, "mon": MON}[d]] = items
    return out


STANDING = pi.Item("Warm connects", 15, 2, "Lane 4", standing=True)


class FakeNotion:
    def __init__(self):
        self.calls = []

    def retrieve_database(self, _):
        return {"data_sources": [{"id": "todo-ds"}]}

    def retrieve_data_source(self, _):
        return {"properties": {"Due": {"id": "d1"}, "Source": {"id": "s1"}}}

    def append_blocks(self, page, children, after=None):
        self.calls.append(("append", page, after))
        return [{"id": "head-1"}]

    def create_view(self, body):
        self.calls.append(("create_view", body))
        return {"id": "view-1", "parent": {"database_id": "db-1"}}

    def update_view(self, view_id, body):
        self.calls.append(("update_view", view_id, body))
        return {}

    def retrieve_view(self, _):
        return {"filter": {"and": []}}


def _run(conn, client, today, posts):
    return pd.run_once(client, connection=conn.same, create=task_tinder.create_plan_task,
                       today=today, post=lambda t: posts.append(t) or True, say=lambda _: None)


@db_only
def test_two_days_end_to_end(conn):
    pi.save(conn, _plan(fri=[STANDING, pi.Item("Write cards", 30, 3, "why")],
                        mon=[STANDING, pi.Item("Publish post 1", 15, 3, None, cut_first=True)]),
            "plan-page")
    client, posts = FakeNotion(), []

    # Friday: two tasks, a post, and the dashboard section.
    assert "2 task(s) created" in _run(conn, client, FRI, posts)
    assert posts == ["Today, Fri Nov 20: 2 tasks · 45 min (budget 180)\n"
                     "Dashboard: <https://www.notion.so/page1>"]
    assert client.calls[0] == ("append", "page-1", "att-1")
    view = client.calls[1][1]
    assert view["create_database"]["position"] == {"type": "after_block", "block_id": "head-1"}
    assert view["filter"]["and"][1] == {"property": "d1", "date": {"on_or_after": "2026-11-20"}}

    # A re-run creates nothing, doesn't post, and only updates the view.
    assert "0 task(s) created" in _run(conn, client, FRI, posts)
    assert len(posts) == 1 and client.calls[-1][0] == "update_view"

    # Saturday isn't a plan day.
    assert "not a plan day" in _run(conn, client, SAT, posts)

    # Monday: the standing task is skipped, the other carried, two new ones made.
    out = _run(conn, client, MON, posts)
    assert "2 task(s) created, 1 carried forward, 1 standing task(s) skipped" in out
    assert posts[-1].splitlines()[:2] == [
        "Fri Nov 20: 0 of 2 done · 0 of 45 min · 1 carried to today",
        "Today, Mon Nov 23: 3 tasks · 60 min (budget 180) · 1 marked 🔻"]
    rows = conn.execute("SELECT t.title, t.status, t.due_date, f.status, f.deadline "
                        "FROM tasks t JOIN follow_ups f ON f.id = t.follow_up_id "
                        "ORDER BY t.id").fetchall()
    assert rows[0][:4] == ("Warm connects", "skipped", FRI, "skipped")
    assert rows[1][:3] == ("Write cards", "open", MON)
    assert [r[4] for r in rows] == [None] * 4  # N-D15: no deadlines
    assert conn.execute("SELECT description FROM tasks WHERE title = 'Write cards'"
                        ).fetchone()[0] == "why"


@db_only
def test_a_later_due_date_is_left_alone(conn):
    pi.save(conn, _plan(fri=[pi.Item("Write cards", 30, 3, None)], mon=[STANDING]), "p")
    _run(conn, FakeNotion(), FRI, [])
    conn.execute("UPDATE tasks SET due_date = '2026-11-30'")  # moved in Notion
    assert "0 carried forward" in _run(conn, FakeNotion(), MON, [])
    assert conn.execute("SELECT due_date FROM tasks WHERE title = 'Write cards'"
                        ).fetchone()[0] == date(2026, 11, 30)


@db_only
def test_reimport_keeps_started_days_and_retires_the_old_plan(conn):
    pi.save(conn, _plan(fri=[STANDING], mon=[STANDING]), "old")
    first = pi.save(conn, _plan(fri=[STANDING], mon=[STANDING]), "page")
    assert first["items"] == 2
    assert conn.execute("SELECT status FROM plans WHERE notion_page_id = 'old'"
                        ).fetchone()[0] == "retired"
    _run(conn, FakeNotion(), FRI, [])
    again = pi.save(conn, _plan(fri=[STANDING, STANDING], mon=[STANDING, STANDING]), "page")
    assert (again["kept_days"], again["items"]) == (1, 2)
    assert conn.execute("SELECT count(*) FROM plan_items i JOIN plans p ON p.id = i.plan_id "
                        "WHERE p.status = 'active' AND i.day = %s", (FRI,)).fetchone()[0] == 1
    skipped = pi.save(conn, _plan(fri=[STANDING], mon=[STANDING]), "page", from_day=MON)
    assert (skipped["skipped_days"], skipped["items"]) == (1, 1)


@db_only
def test_create_plan_task_is_once_per_item(conn):
    pi.save(conn, _plan(fri=[STANDING]), "p")
    item = conn.execute("SELECT id FROM plan_items").fetchone()[0]
    kwargs = dict(plan_item_id=item, title="x", owner="operator", due_date=FRI,
                  okr_initiative_id=None, description=None)
    assert task_tinder.create_plan_task(**kwargs) is not None
    assert task_tinder.create_plan_task(**kwargs) is None
    assert conn.execute("SELECT count(*) FROM tasks").fetchone()[0] == 1

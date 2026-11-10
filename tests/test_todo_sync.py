"""Tests for the Notion To-Do sync (N3, N-S17): projection, neutral titles, the
per-field merge (Notion wins), the per-task plan, imports, and one cycle against a
fake Notion and a fake connection."""

from __future__ import annotations

from datetime import UTC, date, datetime

from agents._lib.notion_client import content_hash
from agents.notion import todo_schema as todo
from agents.notion import todo_sync as ts


def task(tid=1, *, status="open", source="discovery", title="Write about Git-knife",
         page=None, digest=None, synced=None, initiative=None, due=date(2026, 11, 15),
         fields=None):
    return {"id": tid, "title": title, "status": status, "due_date": due,
            "created_at": datetime(2026, 10, 1, 9, tzinfo=UTC), "source_type": source,
            "initiative_page_id": initiative, "notion_page_id": page, "imported": False,
            "content_hash": digest, "synced_done": synced, "synced_fields": fields}


def page(pid, done=False, *, title="Write about Git-knife", due="2026-11-15",
         initiative=(), cos_id=None):
    return {"id": pid, "properties": {
        "Task": {"type": "title", "title": [{"plain_text": title}]},
        "Due": {"type": "date", "date": {"start": due} if due else None},
        "Initiative": {"type": "relation", "relation": [{"id": i} for i in initiative]},
        "Done": {"type": "checkbox", "checkbox": done},
        "CoS id": {"type": "number", "number": cos_id}}}


# --- projection and titles -----------------------------------------------------------


def test_projection_maps_every_column():
    props = todo.projection(task(initiative="init-page"), done=False)
    assert props["Task"]["title"][0]["text"]["content"] == "Write about Git-knife"
    assert props["Initiative"] == {"relation": [{"id": "init-page"}]}
    assert props["Done"] == {"checkbox": False}
    assert props["Due"] == {"date": {"start": "2026-11-15"}}
    assert props["Source"] == {"select": {"name": "discovery"}}
    assert props["Created"] == {"date": {"start": "2026-10-01"}}
    assert props["CoS id"] == {"number": 1}


def test_projection_unassigned_and_unknown_source():
    props = todo.projection(task(source=None, initiative=None), done=True)
    assert props["Initiative"] == {"relation": []}
    assert props["Source"] == {"select": None}
    assert props["Done"] == {"checkbox": True}


def test_prospect_sources_never_send_their_real_title():
    secret = "Follow up with Dana at Acme Corp about the Q1 pilot"
    for source in todo.NEUTRAL_TITLES:
        t = task(7, source=source, title=secret)
        sent = todo.projection(t, done=False)["Task"]["title"][0]["text"]["content"]
        assert secret not in sent and "Acme" not in sent and "Dana" not in sent
        assert sent.endswith("· #7")
    assert todo.title_for(task(7, source="operator", title="Renew domain")) == "Renew domain"


def test_source_labels_cover_every_option():
    options = todo.properties("ds")["Source"]["select"]["options"]
    assert [o["name"] for o in options] == ["discovery", "outreach", "inbound", "operator",
                                            "notion"]


# --- the per-field merge -------------------------------------------------------------


def test_merge_field_cases():
    def m(pg, notion, agreed, has=True):
        return todo.merge_field(pg, notion, agreed, has_agreed=has)
    assert m("a", "a", "x") == "agree"
    assert m("a", "b", "a") == "pull"  # edited in Notion
    assert m("b", "a", "a") == "push"  # changed in Discord only: Discord fills the gap
    assert m("b", "c", "a") == "pull"  # both changed: Notion wins
    assert m("b", "a", None, has=False) == "pull"  # no agreed value yet: Notion wins
    assert m(True, False, True) == "pull" and m(True, False, False) == "push"


def test_neutral_title_tasks_never_merge_their_title():
    assert "title" not in todo.owned_fields(task(source="inbound_lead"))
    assert todo.owned_fields(task()) == todo.OWNED


def test_notion_values_reads_every_owned_field():
    nv = todo.notion_values(page("p", True, title="Ship it", due="2026-12-01T09:00:00",
                                 initiative=["i-1"]))
    assert nv == {"title": "Ship it", "due": "2026-12-01", "initiative": ["i-1"], "done": True}


# --- plan ----------------------------------------------------------------------------

INITS = {"i-1": 3, "i-2": 4}


def _synced(t, done=False):
    """A task whose row was last agreed on with the task's own values."""
    pv = todo.pg_values(t, done=done)
    return {**t, "content_hash": content_hash(todo.fact_props(t)), "synced_fields": pv,
            "synced_done": done}


def _plan(t, pg_page):
    [step] = ts.plan([t], {pg_page["id"]: pg_page} if pg_page else {}, INITS)
    return step


def test_plan_creates_a_row_for_a_new_task():
    step = _plan(task(), None)
    assert step.write == "create" and step.props["CoS id"] == {"number": 1}
    assert step.agreed["done"] is False


def test_plan_skips_an_unchanged_row():
    step = _plan(_synced(task(page="p1")), page("p1"))
    assert step.write == "skip" and set(step.decisions.values()) == {"agree"}


def test_plan_takes_notion_edits_and_writes_nothing_back():
    t = _synced(task(page="p1"))
    step = _plan(t, page("p1", True, title="Write about Git-knife, part 2", due="2026-11-20",
                         initiative=["i-1"]))
    assert step.pulls == {"done": True, "title": "Write about Git-knife, part 2",
                          "due": "2026-11-20", "initiative": ["i-1"]}
    assert step.write == "skip" and step.props == {}  # never overwrites the Notion edit
    assert step.agreed["title"] == "Write about Git-knife, part 2"


def test_plan_pushes_a_discord_change_notion_did_not_touch():
    t = _synced(task(page="p1"))
    t["status"], t["initiative_page_id"] = "completed", "i-2"
    step = _plan(t, page("p1"))
    assert step.decisions["done"] == "push" and step.decisions["initiative"] == "push"
    assert step.props == {"Done": {"checkbox": True},
                          "Initiative": {"relation": [{"id": "i-2"}]}}


def test_plan_notion_wins_when_both_sides_changed():
    t = _synced(task(page="p1"))
    t["initiative_page_id"] = "i-2"  # /initiative in Discord
    step = _plan(t, page("p1", initiative=["i-1"]))  # and a different edit in Notion
    assert step.decisions["initiative"] == "pull" and "Initiative" not in step.props
    assert step.pulls["initiative"] == ["i-1"]


def test_plan_with_no_agreed_values_takes_notion():
    # Rows from before migration 0036 have only synced_done.
    t = {**task(page="p1", synced=False), "content_hash": content_hash(todo.fact_props(task()))}
    step = _plan(t, page("p1", due="2026-12-24"))
    assert step.pulls == {"due": "2026-12-24"} and step.write == "skip"


def test_plan_holds_an_unknown_initiative_or_a_blank_title():
    t = _synced(task(page="p1"))
    step = _plan(t, page("p1", title=" ", initiative=["not-pulled-yet"]))
    assert step.decisions["title"] == step.decisions["initiative"] == "hold"
    assert step.pulls == {} and step.props == {}
    assert step.agreed["initiative"] == []  # unchanged, so it's retried next cycle
    two = _plan(t, page("p1", initiative=["i-1", "i-2"]))
    assert two.decisions["initiative"] == "hold"


def test_plan_never_pulls_or_pushes_a_neutral_title():
    t = _synced(task(page="p1", source="inbound_lead", title="Call Dana at Acme"))
    step = _plan(t, page("p1", title="Something the operator typed"))
    assert "title" not in step.decisions and "Task" not in step.props


def test_plan_recreates_a_row_deleted_in_notion():
    assert _plan(_synced(task(page="gone")), None).write == "create"


def test_plan_rewrites_changed_facts_only():
    t = {**_synced(task(page="p1")), "content_hash": "old"}
    step = _plan(t, page("p1"))
    assert set(step.props) == {"Source", "Created", "CoS id"}


def test_new_rows_are_unmapped_rows_without_a_cos_id():
    tasks = [task(1, page="p1")]
    pages = {"p1": page("p1", cos_id=1), "hand": page("hand"),
             "stale": page("stale", cos_id=99)}
    assert [p["id"] for p in ts.new_rows(tasks, pages)] == ["hand"]


# --- one cycle -----------------------------------------------------------------------


class FakeNotion:
    def __init__(self, rows):
        self.rows = rows  # page id → page
        self.writes = []

    def retrieve_database(self, database_id):
        return {"data_sources": [{"id": "ds-todo"}]}

    def iter_query(self, data_source_id):
        return iter(list(self.rows.values()))

    def create_page(self, parent, properties):
        pid = f"new-{len(self.writes)}"
        self.writes.append(("create", parent["data_source_id"], properties))
        self.rows[pid] = {"id": pid, "properties": properties}
        return {"id": pid}

    def update_page(self, page_id, properties):
        self.writes.append(("update", page_id, properties))


class FakeConn:
    """Serves the To-Do database id, the initiatives, and the task rows; logs writes."""

    def __init__(self, tasks, log):
        self.tasks, self.log = tasks, log

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=()):
        self.log.append((sql, params))

        class R:
            def fetchone(self):
                return ("todo-db",)

            def fetchall(self):
                return list(INITS.items())
        return R()

    def cursor(self, row_factory=None):
        conn = self

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=()):
                conn.log.append((sql, params))
                self.last = sql

            def fetchall(self):
                return conn.tasks
        return Cur()


def test_run_once_merges_imports_and_records():
    tasks = [task(1),                                                   # new: create
             _synced(task(2, page="p2")),                               # ticked in Notion
             _synced(task(3, page="p3", source="inbound_lead", title="Call Dana at Acme")),
             _synced(task(4, page="p4"))]                               # due edited in Notion
    notion = FakeNotion({"p2": page("p2", True), "p3": page("p3", title="x · #3"),
                         "p4": page("p4", due="2026-12-01"),
                         "hand": page("hand", title="Book the venue", initiative=["i-1"]),
                         "blank": page("blank", title="")})
    log, completed, reopened, created = [], [], [], []

    def create(**kw):
        created.append(kw)
        return {"task_id": 60, "follow_up_id": 50,
                "created_at": datetime(2026, 11, 10, tzinfo=UTC)}
    out = ts.run_once(notion, connection=lambda: FakeConn(tasks, log),
                      complete=completed.append, reopen=reopened.append, create=create)
    assert completed == [2] and reopened == []
    # Notion's edits are never written back: only the new row and the import's facts.
    assert [(w[0], w[1]) for w in notion.writes] == [("create", "ds-todo"), ("update", "hand")]
    assert set(notion.writes[1][2]) == {"Source", "Created", "CoS id"}
    assert notion.writes[1][2]["Source"] == {"select": {"name": "notion"}}
    assert ("UPDATE tasks SET due_date = %s WHERE id = %s", ("2026-12-01", 4)) in log
    assert created == [{"title": "Book the venue", "owner": "operator", "due_date": "2026-11-15",
                        "okr_initiative_id": 3, "completed": False}]  # i-1 → 3
    sent = [w[2].get("Task") for w in notion.writes if w[2].get("Task")]
    assert not any("Acme" in t["title"][0]["text"]["content"] for t in sent)
    upserts = [p for sql, p in log if "INSERT INTO notion_sync" in sql]
    assert [p[1] for p in upserts] == ["1", "2", "3", "4", "60"]
    assert "1 rows created, 0 updated, 2 field(s) taken from Notion" in out
    assert "1 row(s) imported as tasks, 1 blank row(s) waiting for a title" in out

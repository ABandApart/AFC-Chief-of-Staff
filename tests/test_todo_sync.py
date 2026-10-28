"""Tests for the Notion To-Do sync (N3): projection, neutral titles, the Done merge,
the per-task plan, and one cycle against a fake Notion and a fake connection."""

from __future__ import annotations

from datetime import UTC, date, datetime

from agents._lib.notion_client import content_hash
from agents.notion import todo_schema as todo
from agents.notion import todo_sync as ts


def task(tid=1, *, status="open", source="discovery", title="Write about Git-knife",
         page=None, digest=None, synced=None, initiative=None):
    return {"id": tid, "title": title, "status": status, "due_date": date(2026, 11, 15),
            "created_at": datetime(2026, 10, 1, 9, tzinfo=UTC), "source_type": source,
            "initiative_page_id": initiative, "notion_page_id": page,
            "content_hash": digest, "synced_done": synced}


def page(pid, done):
    return {"id": pid, "properties": {"Done": {"type": "checkbox", "checkbox": done}}}


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
    assert [o["name"] for o in options] == ["discovery", "outreach", "inbound", "operator"]


# --- the Done merge ------------------------------------------------------------------


def test_merge_done_cases():
    assert todo.merge_done(False, None, None) == "push"  # no row yet
    assert todo.merge_done(True, True, False) == "agree"
    assert todo.merge_done(False, True, False) == "pull"  # ticked in Notion
    assert todo.merge_done(True, False, True) == "pull"  # unticked in Notion
    assert todo.merge_done(True, False, False) == "push"  # checked off in Discord
    assert todo.merge_done(False, True, True) == "push"  # reopened in Discord
    assert todo.merge_done(True, False, None) == "push"  # no agreed value yet


# --- plan ----------------------------------------------------------------------------


def _synced(t, done):
    return {**t, "content_hash": content_hash(todo.projection(t, done=done)), "synced_done": done}


def test_plan_creates_a_row_for_a_new_task():
    [step] = ts.plan([task()], {})
    assert (step.write, step.done, step.final_done) == ("create", "push", False)


def test_plan_skips_an_unchanged_row():
    t = _synced(task(page="p1"), False)
    [step] = ts.plan([t], {"p1": page("p1", False)})
    assert (step.write, step.done) == ("skip", "agree")


def test_plan_takes_a_notion_tick_and_rewrites_the_row():
    t = _synced(task(page="p1"), False)
    [step] = ts.plan([t], {"p1": page("p1", True)})
    assert (step.done, step.final_done, step.write) == ("pull", True, "update")


def test_plan_pushes_a_discord_check_off():
    t = _synced(task(page="p1"), False)
    t["status"] = "completed"
    [step] = ts.plan([t], {"p1": page("p1", False)})
    assert (step.done, step.final_done, step.write) == ("push", True, "update")
    assert step.props["Done"] == {"checkbox": True}


def test_plan_recreates_a_row_deleted_in_notion():
    t = _synced(task(page="gone"), False)
    [step] = ts.plan([t], {})
    assert step.write == "create"


def test_plan_pushes_a_new_initiative():
    t = _synced(task(page="p1"), False)
    t["initiative_page_id"] = "init-page"
    [step] = ts.plan([t], {"p1": page("p1", False)})
    assert step.write == "update"


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
    """Serves the To-Do database id and the task rows; records notion_sync writes."""

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
        return R()

    def cursor(self, row_factory=None):
        tasks = self.tasks

        class Cur:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

            def execute(self, sql, params=()):
                pass

            def fetchall(self):
                return tasks
        return Cur()


def test_run_once_backfills_merges_and_records(mocker):
    tasks = [task(1), _synced(task(2, page="p2"), False), task(3, source="inbound_lead",
                                                                title="Call Dana at Acme")]
    notion = FakeNotion({"p2": page("p2", True), "stray": page("stray", False)})
    log = []
    completed, reopened = [], []
    out = ts.run_once(notion, connection=lambda: FakeConn(tasks, log),
                      complete=completed.append, reopen=reopened.append)
    assert completed == [2] and reopened == []
    kinds = [(w[0], w[1]) for w in notion.writes]
    assert kinds == [("create", "ds-todo"), ("update", "p2"), ("create", "ds-todo")]
    sent_titles = [w[2]["Task"]["title"][0]["text"]["content"] for w in notion.writes]
    assert not any("Acme" in t for t in sent_titles)
    upserts = [p for sql, p in log if "INSERT INTO notion_sync" in sql]
    assert [(p[1], p[4]) for p in upserts] == [("1", False), ("2", True), ("3", False)]
    assert "2 rows created, 1 updated, 1 Done change(s) taken from Notion" in out
    assert "1 row(s) in Notion with no task" in out

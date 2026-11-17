"""Tests for the Now callouts on OKR pages (PRD-notion-integration.md N10 Part A)."""

from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal

from agents._lib import kr_metrics as km
from agents._lib.notion_client import NotionError
from agents.notion import okr_pages as op


def result(kr_id=1, name="Discovery calls booked", metric="calls_booked", current=4,
           target=20, status=km.AT_RISK, progress=0.2):
    return km.Result(kr_id, name, metric, Decimal(current) if current is not None else None,
                     Decimal(target), km.Score(status, None, progress))


def test_kr_text_has_value_progress_status_history_and_method():
    text = op.kr_text(result(), "count", [Decimal(1), None, Decimal(3), Decimal(4)])
    lines = text.splitlines()
    assert lines[0] == "Now: 4 of 20 (20%) · 🟡 At risk"
    assert lines[1] == "Last weeks: 1 → — → 3 → 4"
    assert "discovery_call_booked" in lines[2]


def test_kr_text_for_a_manual_kr_and_money_and_percent_units():
    manual = result(metric="manual", current=None, status=km.NO_DATA, progress=None)
    text = op.kr_text(manual, "hours", [])
    assert text.splitlines()[0] == "Now: — of 20 hours · ⚪ No data"
    assert "Entered by hand" in text and "Last weeks" not in text
    assert op._fmt(Decimal("15000"), "$") == "$15,000"
    assert op._fmt(Decimal("92.5"), "%") == "92.5%"


def test_initiative_and_objective_texts():
    init = {"open_todos": 3, "done_todos": 5}
    text = op.initiative_text(init, result(), "count", 240)
    assert text.splitlines() == ["Moves: Discovery calls booked, now 4 of 20 · 🟡 At risk",
                                 "To-dos: 3 open, 5 done", "Plan this week: 240 min"]
    assert op.initiative_text(init, None, None, 0).startswith("To-dos")
    obj = op.objective_text([result(), result(2, "Proposals sent", "proposals_sent", 9, 10,
                                              km.ON_TRACK, 0.9)], {1: "count", 2: "count"})
    assert "Weakest: Discovery calls booked" in obj
    assert op.objective_text([], {}) == "No active Key Results."


def test_status_emoji_comes_from_the_label():
    assert op.status_emoji(result()) == "🟡"


class FakeClient:
    def __init__(self, fail_update=False):
        self.updated, self.appended, self.fail = [], [], fail_update

    def update_block(self, block_id, body):
        if self.fail:
            raise NotionError("gone", status=404)
        self.updated.append(block_id)

    def append_blocks(self, page_id, children, *, after=None, at_start=False):
        assert at_start
        self.appended.append(page_id)
        return [{"id": f"blk-{page_id}"}]


class FakeConn:
    def __init__(self, known):
        self.known, self.inserts = known, []

    def execute(self, sql, params=()):
        if sql.lstrip().startswith("SELECT"):
            rows = list(self.known.items())
            return type("R", (), {"fetchall": lambda _s: rows})()
        self.inserts.append(params)
        return None


def test_push_updates_known_callouts_and_creates_missing_ones(monkeypatch):
    conn = FakeConn({"pA:now": "blk-old"})

    @contextmanager
    def connection():
        yield conn
    monkeypatch.setattr(op, "pages", lambda _c, _w: [("pA", "🎯", "a"), ("pB", "🟢", "b")])
    client = FakeClient()
    assert op.push(client, connection, object()) == 2
    assert client.updated == ["blk-old"] and client.appended == ["pB"]
    assert conn.inserts == [("okr_page", "pB:now", "blk-pB")]


def test_push_recreates_a_callout_the_operator_deleted(monkeypatch):
    conn = FakeConn({"pA:now": "blk-old"})

    @contextmanager
    def connection():
        yield conn
    monkeypatch.setattr(op, "pages", lambda _c, _w: [("pA", "🎯", "a")])
    client = FakeClient(fail_update=True)
    assert op.push(client, connection, object()) == 1
    assert client.appended == ["pA"]

"""Tests for Task Tinder cards in the To-Do database (PRD-notion-integration.md N9 I1)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from agents._lib.notion_client import content_hash
from agents.notion import cards_sync as cs
from agents.notion import operator
from agents.notion import todo_schema as todo
from agents.notion import todo_sync as ts

TODAY = date(2026, 11, 16)
ME, BOT, OTHER = "user-op", "user-bot", "user-x"
ALLOWED = frozenset({ME, BOT})


def card(i=1, **kw):
    base = {"id": i, "proposed_action": "Read the Acme report", "source_type": "discovery",
            "evidence_text": "Found in Tartt", "confidence": 0.7, "status": "pending",
            "deferred_until": None, "due_date": None,
            "created_at": datetime(2026, 11, 10, tzinfo=UTC), "initiative_page_id": None,
            "notion_page_id": None, "content_hash": None}
    return {**base, **kw}


def mapped(c):
    return {**c, "notion_page_id": "p1", "content_hash": content_hash(cs.facts(c))}


def page(pid="p1", *, decision=None, until=None, title="Read the Acme report", due=None,
         initiative=(), editor=ME, cos_id=None, card_id=1):
    return {"id": pid, "last_edited_by": {"object": "user", "id": editor}, "properties": {
        "Task": {"title": [{"plain_text": title}]},
        "Due": {"date": {"start": due} if due else None},
        "Initiative": {"relation": [{"id": i} for i in initiative]},
        "Done": {"checkbox": False},
        "Decision": {"select": {"name": decision} if decision else None},
        "Defer until": {"date": {"start": until} if until else None},
        "Card id": {"number": card_id},
        "CoS id": {"number": cos_id}}}


def one(c, p, resurfaced=frozenset()):
    [step] = cs.plan([c], {p["id"]: p} if p else {}, ALLOWED, TODAY, resurfaced)
    return step


def test_new_card_row_carries_card_id_and_no_cos_id():
    props = cs.new_row(card())
    assert props["Card id"] == {"number": 1} and "CoS id" not in props
    assert props["Source"] == {"select": {"name": "discovery"}}
    assert "Decision" not in props
    deferred = cs.new_row(card(status="deferred", deferred_until=date(2026, 11, 20)))
    assert deferred["Decision"] == {"select": {"name": "Defer"}}
    assert deferred["Defer until"] == {"date": {"start": "2026-11-20"}}


def test_card_rows_are_never_imported_as_tasks():
    assert todo.is_card(page())
    assert not todo.is_card(page(cos_id=9))          # accepted: now a task row
    hand = {"id": "hand", "properties": {"Task": {"title": [{"plain_text": "x"}]},
                                         "CoS id": {"number": None}}}
    assert [p["id"] for p in ts.new_rows([], {"c": page("c"), "hand": hand})] == ["hand"]


def test_plan_creates_skips_and_takes_decisions():
    assert one(card(), None).action == "create"
    assert one(mapped(card()), page()).action == "skip"
    assert one(mapped(card()), page(decision="Accept")).action == "accept"
    assert one(mapped(card()), page(decision="Decline")).action == "decline"


def test_defer_without_a_date_defaults_to_a_week():
    step = one(mapped(card()), page(decision="Defer"))
    assert step.action == "defer" and step.values["defer_until"] == "2026-11-23"
    assert step.props["Defer until"] == {"date": {"start": "2026-11-23"}}
    dated = one(mapped(card()), page(decision="Defer", until="2026-12-01"))
    assert dated.values["defer_until"] == "2026-12-01" and "Defer until" not in dated.props


def test_a_deferred_card_stays_deferred_and_clearing_decision_reopens_it():
    c = mapped(card(status="deferred", deferred_until=date(2026, 11, 30)))
    assert one(c, page(decision="Defer", until="2026-11-30")).action == "skip"
    assert one(c, page()).action == "reopen"


def test_a_card_back_from_deferral_has_its_decision_cleared():
    step = one(mapped(card()), page(decision="Defer", until="2026-11-16"), frozenset({1}))
    assert step.action == "refresh"
    assert step.props == {"Decision": {"select": None}, "Defer until": {"date": None}}


def test_decisions_by_someone_else_are_refused():
    step = one(mapped(card()), page(decision="Accept", editor=OTHER))
    assert step.action == "refuse" and "someone other" in step.note
    assert one(mapped(card()), page(decision="Accept", editor=BOT)).action == "accept"


def test_changed_facts_are_refreshed():
    c = {**mapped(card()), "evidence_text": "New evidence"}
    step = one(c, page())
    assert step.action == "refresh" and "Evidence" in step.props


def test_foreign_edit_rule():
    assert operator.foreign_edit({"last_edited_by": {"id": OTHER}}, ALLOWED)
    assert not operator.foreign_edit({"last_edited_by": {"id": ME}}, ALLOWED)
    assert not operator.foreign_edit({}, ALLOWED)

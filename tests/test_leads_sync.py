"""Tests for the Notion Leads sync (PRD-notion-integration.md N8).

The pure tests always run. The database test needs Postgres: set PLAN_TEST_DSN
(for example `dbname=postgres`). It builds the tables it touches as temporary
tables, so no real table is read or written, and is skipped when unset.
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import date

import pytest

from agents._lib import task_tinder
from agents._lib.notion_client import content_hash
from agents.notion import leads_schema as leads
from agents.notion import leads_sync as ls

TODAY = date(2026, 11, 16)
ROOM = {"cold_live": 11, "cold_ceiling": 150, "reengagement_live": 0,
        "reengagement_ceiling": 3}


def target(i=1, **kw):
    base = {"id": i, "status": "candidate", "company_name": "Acme Learning",
            "company_domain": "acmelearning.com", "company_url": None,
            "sector": "corporate_l_and_d",
            "headcount": 40, "hq_location": "Austin, TX", "ownership_type": "founder_owned",
            "total_raised_usd": None, "last_round_type": None, "last_round_at": None,
            "founded_year": 2014, "contact_name": "Dana Reyes", "contact_role": "CEO",
            "contact_email": "dana@acmelearning.com", "contact_linkedin_url": None,
            "stage": None, "function_state": None, "start_on": None, "last_contacted": None,
            "next_touch": None, "subject_line": None, "body_filled": None, "packet_ready": None,
            "notion_page_id": None, "content_hash": None, "synced_fields": None}
    return {**base, **kw}


def page(pid, *, status="New", start=None, stage=None, fs=None):
    def sel(v):
        return {"type": "select", "select": {"name": v} if v else None}
    return {"id": pid, "properties": {
        "Status": {"type": "status", "status": {"name": status}},
        "Start on": {"type": "date", "date": {"start": start} if start else None},
        "Stage": sel(stage), "Function state": sel(fs)}}


def synced(t, capacity_free=True):
    """The target as it is after a sync that agreed on everything."""
    return {**t, "notion_page_id": "p1",
            "content_hash": content_hash(leads.fact_props(t, capacity_free=capacity_free)),
            "synced_fields": {f: leads.pg_values(t)[f] for f in leads.owned_fields(t)}}


def one(t, pg_page, free=True):
    [step] = ls.plan([t], {pg_page["id"]: pg_page} if pg_page else {}, capacity_free=free)
    return step


# --- schema ------------------------------------------------------------------------


def test_every_postgres_status_has_a_label_and_settable_ones_round_trip():
    for status in ("candidate", "in_sequence", "conversation", "call_booked", "engaged",
                   "dropped", "opted_out", "watchlist", "lost_to_hire", "archived"):
        assert status in leads.STATUS_LABEL
    for label, status in leads.SETTABLE.items():
        assert leads.STATUS_LABEL[status] == label


def test_status_new_contacted_and_parked_are_refused():
    for label in ("New", "Contacted", "Parked"):
        assert "can't be set" in leads.refusal("status", label)
    assert leads.refusal("status", "Opted out") is None
    assert leads.refusal("stage", "pre_seed") and leads.refusal("stage", None) is None
    assert leads.refusal("function_state", "under_led") is None


def test_schema_problems_names_missing_columns_and_options():
    props = {n: {"type": k, k: {}} for n, k in leads.COLUMNS.items()}
    props["Status"] = {"type": "status", "status": {"options": [{"name": "New"}]}}
    props["Channel"] = {"type": "select", "select": {"options": [{"name": "Outreach"}]}}
    props["Stage"] = {"type": "select", "select": {"options": [
        {"name": s} for s in leads.STAGES]}}
    props["Function state"] = {"type": "select", "select": {"options": [
        {"name": s} for s in leads.FUNCTION_STATES]}}
    del props["Message"]
    problems = leads.schema_problems(props)
    assert any("'Message'" in p for p in problems)
    assert any(p.startswith("Status option(s) missing") and "Opted out" in p for p in problems)
    assert len(problems) == 2


def test_background_line_skips_missing_fields():
    assert leads.background(target()) == \
        "corporate l and d · 40 staff · Austin, TX · founder owned · founded 2014"
    assert leads.background({}) == ""
    funded = target(total_raised_usd=12_000_000, last_round_type="series_a",
                    last_round_at=date(2025, 3, 1))
    assert "raised $12M (last: series_a, 2025-03-01)" in leads.background(funded)


def test_facts_use_company_when_no_contact_and_split_long_messages():
    t = target(contact_name=None, body_filled="x" * 4500, subject_line="Hi")
    facts = leads.fact_props(t, capacity_free=True)
    assert facts["Name"]["title"][0]["text"]["content"] == "Acme Learning"
    assert len(facts["Message"]["rich_text"]) == 3
    assert facts["Website"] == {"url": "https://acmelearning.com"}
    assert facts["Channel"] == {"select": {"name": "Outreach"}}
    assert "Notes" not in facts and "Next step" not in facts


def test_ready_to_start_needs_only_function_state_and_room():
    assert not leads.fact_props(target(), capacity_free=True)["Ready to start"]["checkbox"]
    ready = target(function_state="under_led")             # no stage (N8 R1)
    assert leads.fact_props(ready, capacity_free=True)["Ready to start"]["checkbox"]
    assert not leads.fact_props(ready, capacity_free=False)["Ready to start"]["checkbox"]


def test_stage_and_function_state_are_cos_written_once_in_sequence():
    live = target(status="in_sequence", stage="mature", function_state="vacant_seat")
    assert leads.owned_fields(live) == ("status", "start_on")
    facts = leads.fact_props(live, capacity_free=True)
    assert facts["Function state"] == {"select": {"name": "vacant_seat"}}
    assert "Function state" not in leads.fact_props(target(), capacity_free=True)


# --- plan --------------------------------------------------------------------------


def test_plan_creates_a_row_with_every_column():
    step = one(target(), None)
    assert step.write == "create"
    assert step.props["Status"] == {"status": {"name": "New"}}
    assert step.props["CoS id"] == {"number": 1}
    assert set(step.agreed) == set(leads.MERGED)


def test_plan_skips_an_unchanged_row():
    step = one(synced(target()), page("p1"))
    assert step.write == "skip" and set(step.decisions.values()) == {"agree"}


def test_plan_takes_the_operators_edits():
    t = synced(target())
    step = one(t, page("p1", status="Opted out", start="2026-11-17", fs="under_led"))
    assert step.pulls == {"status": "Opted out", "start_on": "2026-11-17",
                          "function_state": "under_led"}
    assert step.write == "skip"


def test_plan_refuses_a_status_the_operator_cant_set_and_restores_it():
    step = one(synced(target()), page("p1", status="Contacted"))
    assert step.decisions["status"] == "refuse" and "status" not in step.pulls
    assert step.props["Status"] == {"status": {"name": "New"}}
    assert step.refused and "Contacted" in step.refused[0]


def test_plan_pushes_a_cos_change_notion_didnt_make():
    before = synced(target())
    now = {**before, "status": "in_sequence", "function_state": "under_led"}
    step = one(now, page("p1"))
    assert step.decisions["status"] == "push"
    assert step.props["Status"] == {"status": {"name": "Contacted"}}


# --- starts, tasks, report ----------------------------------------------------------


def test_start_problem_needs_function_state_not_stage():
    t = {**target(function_state="self_covered"), "is_reengagement": False}
    assert ls.start_problem(t, ROOM) is None
    assert "function_state" in ls.start_problem({**t, "function_state": None}, ROOM)
    assert "150/150" in ls.start_problem(t, {**ROOM, "cold_live": 150})


def test_old_triggers_are_reanchored_on_the_start_day():
    assert ls.needs_reanchor(date(2026, 8, 27), TODAY)
    assert not ls.needs_reanchor(date(2026, 11, 12), TODAY)


def test_report_is_silent_with_nothing_to_say():
    assert ls.report([], [], [], [], 3) is None
    text = ls.report(["Acme"], ["Beta: function_state not set"], [], ["Gamma"], 2)
    assert text.splitlines() == [
        "Outreach: 1 sequence(s) started (Acme)",
        "Not started: Beta: function_state not set",
        "Delete the unsent Gmail draft(s) for: Gamma",
        "2 outreach task(s) added to To-Do",
    ]


def test_touch_title():
    assert ls.touch_title({"company_name": "Acme", "slot": 2}) == "Outreach: Acme, send touch 2"


# --- database ----------------------------------------------------------------------

DSN = os.environ.get("PLAN_TEST_DSN")
db_only = pytest.mark.skipif(not DSN, reason="PLAN_TEST_DSN not set")

_TABLES = """
CREATE TEMP TABLE outreach_touches (id bigserial PRIMARY KEY);
CREATE TEMP TABLE task_candidates (id bigserial PRIMARY KEY, proposed_action text,
    source_type text, source_ref text, evidence_text text, confidence real, status text,
    decided_at timestamptz, due_date date);
CREATE TEMP TABLE follow_ups (id bigserial PRIMARY KEY, owner text, action text,
    deadline date, status text, escalation_level smallint);
CREATE TEMP TABLE tasks (id bigserial PRIMARY KEY, title text, description text, owner text,
    source_candidate_id bigint, status text, follow_up_id bigint, due_date date);
INSERT INTO outreach_touches DEFAULT VALUES;
"""


@db_only
def test_create_outreach_task_once_per_touch(monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(DSN, autocommit=True) as c:
        c.execute("SET search_path = pg_temp")
        c.execute(_TABLES)

        @contextmanager
        def same():
            yield c
        monkeypatch.setattr(task_tinder.db, "connection", same)
        kw = {"touch_id": 1, "title": "Outreach: Acme, send touch 1", "owner": "operator",
              "due_date": TODAY, "evidence": "Touch 1 to Dana."}
        task_id = task_tinder.create_outreach_task(**kw)
        assert task_id is not None
        assert task_tinder.create_outreach_task(**kw) is None          # a re-run
        assert task_tinder.create_outreach_task(**{**kw, "touch_id": 99}) is None
        row = c.execute("SELECT c.source_type, c.status, f.deadline, t.due_date "
                        "FROM tasks t JOIN task_candidates c ON c.id = t.source_candidate_id "
                        "JOIN follow_ups f ON f.id = t.follow_up_id").fetchall()
        assert row == [("outreach_touch", "accepted", None, TODAY)]   # N-D18: no deadline

"""Tests for the OKR pull (pure — page parsing, validation, notices, and the Notion read).

The SQL in `apply` was scratch-tested against a throwaway database built from
migration 0033 (see the N2 build notes in PRD-notion-integration.md).
"""

from __future__ import annotations

from agents.notion import okr_pull as p
from agents.notion import okr_schema as s


def _title(text):
    return {"type": "title", "title": [{"plain_text": text}]}


def _select(name):
    return {"type": "select", "select": {"name": name} if name else None}


def _number(n):
    return {"type": "number", "number": n}


def _rel(*ids):
    return {"type": "relation", "relation": [{"id": i} for i in ids], "has_more": False}


def objective(pid, name="Keep the pipeline full", quarter="2026-Q4", status="Active"):
    return {"object": "page", "id": pid, "last_edited_time": "2026-10-28T12:00:00.000Z",
            "properties": {"Name": _title(name), "Quarter": _select(quarter),
                           "Order": _number(2), "Status": _select(status)}}


def key_result(pid, *objectives, name="Proposals sent", metric="proposals_sent", target=10):
    return {"object": "page", "id": pid, "last_edited_time": "2026-10-28T12:00:00.000Z",
            "properties": {"Name": _title(name), "Objective": _rel(*objectives),
                           "Metric": _select(metric), "Target": _number(target),
                           "Unit": _select("count"), "Direction": _select("Higher is better"),
                           "Manual value": _number(None), "Status": _select("Active")}}


def initiative(pid, *krs, name="Ship the proposal template", status="Active"):
    return {"object": "page", "id": pid, "last_edited_time": "2026-10-28T12:00:00.000Z",
            "properties": {"Name": _title(name), "Key Result": _rel(*krs),
                           "Status": _select(status),
                           "Owner": {"type": "people", "people": [{"name": "Barry"}]}}}


def _pages(objectives=(), krs=(), inits=()):
    return {s.OBJECTIVES: list(objectives), s.KEY_RESULTS: list(krs),
            s.INITIATIVES: list(inits)}


# --- parsing -------------------------------------------------------------------------


def test_parse_maps_every_property():
    o = p.parse_objective(objective("o1"))
    assert (o.page_id, o.name) == ("o1", "Keep the pipeline full")
    assert o.fields == {"quarter": "2026-Q4", "sort_order": 2, "status": "Active"}
    k = p.parse_key_result(key_result("k1", "o1"))
    assert k.links == ["o1"]
    assert k.fields == {"metric": "proposals_sent", "target": 10, "unit": "count",
                        "direction": "Higher is better", "manual_value": None,
                        "status": "Active"}
    i = p.parse_initiative(initiative("i1", "k1"))
    assert i.links == ["k1"]
    assert i.fields == {"status": "Active", "owner_name": "Barry"}
    assert i.edited_at == "2026-10-28T12:00:00.000Z"


def test_parse_tolerates_blank_and_missing_properties():
    page = {"object": "page", "id": "k9", "properties": {"Name": _title("")}}
    k = p.parse_key_result(page)
    assert k.name == "(untitled)"
    assert k.links == []
    assert k.fields["metric"] is None and k.fields["target"] is None


# --- validation ----------------------------------------------------------------------


def _problems(pages):
    objectives, krs, inits = p.parse_all(pages)
    return {r.page_id: r.problem for r in (*objectives, *krs, *inits)}


def test_well_formed_pages_have_no_problems():
    got = _problems(_pages([objective("o1")], [key_result("k1", "o1")],
                           [initiative("i1", "k1")]))
    assert got == {"o1": None, "k1": None, "i1": None}


def test_key_result_link_problems():
    got = _problems(_pages(
        [objective("o1"), objective("o2")],
        [key_result("none"), key_result("two", "o1", "o2"), key_result("stray", "elsewhere")],
    ))
    assert got["none"] == "no Objective"
    assert got["two"] == "links to 2 Objectives (the limit is 1)"
    assert got["stray"] == "its Objective is not in the Objectives database"


def test_initiative_link_problems():
    got = _problems(_pages([objective("o1")], [key_result("k1", "o1")],
                           [initiative("none"), initiative("two", "k1", "k1b")]))
    assert got["none"] == "no Key Result"
    assert got["two"] == "links to 2 Key Results (the limit is 1)"


def test_metric_problems():
    got = _problems(_pages([objective("o1")], [
        key_result("blank", "o1", metric=None),
        key_result("typo", "o1", metric="propsals_sent"),
        key_result("manual", "o1", metric="manual"),
    ]))
    assert got["blank"] == "no Metric"
    assert got["typo"] == "unknown Metric 'propsals_sent'"
    assert got["manual"] is None


def test_quarter_problems_and_any_well_formed_quarter_is_accepted():
    got = _problems(_pages([objective("blank", quarter=None), objective("bad", quarter="Q1 2027"),
                            objective("later", quarter="2028-Q3")]))
    assert got["blank"] == "no Quarter"
    assert got["bad"] == "Quarter 'Q1 2027' is not YYYY-Qn"
    assert got["later"] is None


def test_problems_combine():
    got = _problems(_pages([], [key_result("k1", metric="nope")]))
    assert got["k1"] == "no Objective; unknown Metric 'nope'"


# --- notices -------------------------------------------------------------------------


def test_notice_lists_each_page_with_a_link():
    text = p.notice_text([("Key Results", "Proposals sent", "no Objective", "ab-cd")])
    assert "1 page(s)" in text
    assert "• Key Results · Proposals sent: no Objective · <https://www.notion.so/abcd>" in text


def test_notice_caps_its_lines():
    items = [("Key Results", f"KR {n}", "no Metric", f"id{n}") for n in range(20)]
    text = p.notice_text(items)
    assert text.count("•") == p.MAX_NOTICE_LINES
    assert "…and 5 more." in text
    assert len(text) < 2000


# --- Notion read ---------------------------------------------------------------------


class FakeClient:
    def __init__(self, pages):
        self.pages = pages  # database name → pages

    def retrieve_database(self, database_id):
        return {"data_sources": [{"id": f"ds-{database_id}"}]}

    def iter_query(self, data_source_id):
        return iter(self.pages[data_source_id.removeprefix("ds-db-")])


def test_fetch_reads_each_database_through_its_data_source_and_drops_trash():
    trashed = {**objective("o2"), "in_trash": True}
    client = FakeClient(_pages([objective("o1"), trashed], [key_result("k1", "o1")]))
    got = p.fetch(client, {name: f"db-{name}" for name in s.DATABASE_ORDER})
    assert [pg["id"] for pg in got[s.OBJECTIVES]] == ["o1"]
    assert [pg["id"] for pg in got[s.KEY_RESULTS]] == ["k1"]
    assert got[s.INITIATIVES] == []


def test_read_database_ids_requires_setup():
    class Conn:
        def execute(self, sql, params):
            class R:
                def fetchall(self):
                    return [("okr_objectives", "a"), ("okr_key_results", "b")]
            return R()

    try:
        p.read_database_ids(Conn())
    except RuntimeError as e:
        assert "okr_initiatives" in str(e) and "cli.notion_setup" in str(e)
    else:
        raise AssertionError("expected RuntimeError")

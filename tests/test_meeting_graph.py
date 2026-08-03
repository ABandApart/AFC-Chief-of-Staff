"""Unit tests for the structured Meeting builder (Track C hybrid).

The `add_data_points` insertion is a cognee call (runtime-probed by
`agents/test/ontology_shape.py`); here we pin the pure mapping and — crucially —
the **deterministic ids** that make entity resolution work.
"""

from __future__ import annotations

from agents._lib import meeting_graph as mg

NOTE = {
    "id": "not_abc123def45678",
    "title": "Northwind discovery call",
    "created_at": "2026-07-20T15:00:00Z",
    "owner": {"name": "Barry Baldwin", "email": "barry@aiadaptive.co"},
    "calendar_event": {"event_title": "Northwind sync",
                       "scheduled_start_time": "2026-07-20T16:00:00Z"},
    "attendees": [
        {"name": "Priya Shah", "email": "priya@northwind.com"},
        {"name": "Barry Baldwin", "email": "barry@aiadaptive.co"},
        {"name": None, "email": None},  # empty → skipped
    ],
    "summary_markdown": "- Wants Tuesday check-ins",
}


def test_build_meeting_core_fields():
    m = mg.build_meeting_datapoint(NOTE)
    assert m.title == "Northwind discovery call"
    assert m.summary == "- Wants Tuesday check-ins"
    assert m.meeting_date == "2026-07-20T16:00:00Z"     # calendar start
    assert [p.name for p in m.participants] == ["Priya Shah", "Barry Baldwin"]  # empty skipped


def test_meeting_id_is_deterministic_and_note_derived():
    a = mg.build_meeting_datapoint(NOTE)
    b = mg.build_meeting_datapoint(NOTE)
    assert a.id == b.id == mg.meeting_id("not_abc123def45678")
    assert mg.meeting_id("not_other") != a.id


def test_person_id_by_email_case_insensitive():
    assert mg.person_id("Priya Shah", "priya@northwind.com") == \
        mg.person_id("P. Shah", "PRIYA@NORTHWIND.COM")   # email is the key, lowercased


def test_person_id_falls_back_to_name_without_email():
    assert mg.person_id("Priya Shah", "") == mg.person_id("priya   shah", "")  # normalized name
    assert mg.person_id("Priya Shah", "") != mg.person_id("Someone Else", "")


def test_same_person_resolves_across_meetings():
    # Priya in two different notes → the SAME Person id (entity resolution)
    other = {**NOTE, "id": "not_zzz999", "attendees": [
        {"name": "Priya Shah", "email": "priya@northwind.com"}]}
    p_here = mg.build_meeting_datapoint(NOTE).participants[0]
    p_there = mg.build_meeting_datapoint(other).participants[0]
    assert p_here.id == p_there.id


def test_participants_deduped_within_a_meeting():
    dup = {**NOTE, "attendees": [
        {"name": "Priya Shah", "email": "priya@northwind.com"},
        {"name": "Priya (mobile)", "email": "priya@northwind.com"},  # same email → one node
    ]}
    people = mg.build_meeting_datapoint(dup).participants
    assert len(people) == 1


def test_title_falls_back_to_calendar_then_untitled():
    assert mg.build_meeting_datapoint({**NOTE, "title": None}).title == "Northwind sync"
    bare = mg.build_meeting_datapoint({"id": "not_bare"})
    assert bare.title == "(untitled meeting)" and bare.summary == ""

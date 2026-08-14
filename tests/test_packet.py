"""Unit tests for packet assembly (Track O, `_lib/packet.py`).

Everything here is pure — no DB, no network, and by design **no LLM**, which is
itself asserted: packet assembly must not be able to fail from a provider outage.

The rules with teeth:

  * **`ready` is the R1/R19 guard.** An unresolved slot must block, and stale
    evidence must block, because both produce a specific confident falsehood in
    a founder's inbox — one visible ("[Client 1]"), one not ("open 56 days" on a
    req that came down).
  * **`observed` slots must NOT block** — they are the Gate 2 sentence, and a
    packet with its observation open is a packet doing its job.
  * **Stale evidence is excluded from the arithmetic**, not merely annotated.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from agents._lib import packet, selector

TODAY = date(2026, 8, 14)
TRIGGER = date(2026, 6, 10)

ARITHMETIC = {
    "search": {"duration_days_low": 90, "duration_days_high": 120,
               "ramp_days": 90, "visible_vacancy_days": 60},
    "impact": {"unled_quarters": 2},
    "freshness": {"ageing_after_days": 7, "stale_after_days": 14},
}
SENDER = {"name": "Barry"}

TARGET = {
    "id": 1, "company_name": "Cadence Health", "contact_first_name": "Marcus",
    "contact_role": "Founder", "function": "revenue", "trigger_date": TRIGGER,
    "stage": "series_a",
}


def _fact(fid=1, kind="open_role", first_seen_days=56, last_seen_days=0,
          freshness="fresh", title="VP Revenue"):
    return {
        "id": fid, "fact_kind": kind, "freshness": freshness,
        "first_seen_at": TODAY - timedelta(days=first_seen_days),
        "last_seen_at": TODAY - timedelta(days=last_seen_days),
        "payload": {"title": title},
    }


TEMPLATE = selector.Template(
    code="t10", title="T10", section="Series A", when_to_use="",
    subject="Saw you are hiring a [Role Title]",
    body=("Hi [First Name],\n\n[Company Name] is hiring a [Role Title]. "
          "Posted about [X] weeks ago.\n\nI run [function] fractionally.\n\n"
          "[Your Name]"),
    failure_mode="Positioning yourself as an alternative to the hire.",
)
TOUCH = {"id": 9, "template_code": "t10", "slot": 1}


def _assemble(evidence, target=None, template=None, **kw):
    return packet.assemble_packet(
        TOUCH, target or TARGET, evidence, today=TODAY,
        templates={"t10": template or TEMPLATE},
        config=selector.load_config(),
        arithmetic_config=ARITHMETIC, sender=SENDER, **kw,
    )


# --- slot windows -------------------------------------------------------------


def test_windows_anchor_on_the_trigger_not_the_previous_send():
    w = packet.touch_windows(TRIGGER)
    assert w[1]["window_opens"] == TRIGGER
    assert w[1]["due_date"] == TRIGGER + timedelta(days=3)
    # Slot 5 is the day-60 hinge; every window measures from trigger_date, which
    # is why snoozing one touch never shifts the others.
    assert w[5]["window_opens"] == TRIGGER + timedelta(days=60)
    assert w[5]["window_closes"] == TRIGGER + timedelta(days=90)


def test_all_five_slots_have_ordered_windows():
    for window in packet.touch_windows(TRIGGER).values():
        assert window["window_opens"] <= window["due_date"] <= window["window_closes"]


# --- arithmetic ---------------------------------------------------------------


def test_arithmetic_matches_the_specs_worked_example():
    a = packet.compute_arithmetic(_fact(), ARITHMETIC, today=TODAY)
    assert a["has_arithmetic"] is True
    assert a["posting_age_days"] == 56
    assert a["confirmed_days_ago"] == 0
    assert a["search_days_low"] == 90 and a["search_days_high"] == 120
    # first seen 2026-06-19 + 120 days -> 2026-10-17 -> Q4
    assert a["unled_through_quarter"] == "Q4 2026"


def test_arithmetic_is_shaped_even_with_no_usable_fact():
    # Consumers should never have to tell "no arithmetic" from "missing key".
    a = packet.compute_arithmetic(None, ARITHMETIC, today=TODAY)
    assert a["has_arithmetic"] is False
    assert a["search_days_low"] == 90


def test_rendered_arithmetic_reads_like_the_spec_example():
    a = packet.compute_arithmetic(_fact(), ARITHMETIC, today=TODAY)
    text = packet.render_arithmetic(a, "revenue")
    assert "56 days" in text and "confirmed open today" in text
    assert "90–120 days" in text
    assert "Revenue is effectively unled through Q4 2026" in text


def test_rendered_arithmetic_says_so_when_there_is_none():
    a = packet.compute_arithmetic(None, ARITHMETIC, today=TODAY)
    assert "No open-role evidence" in packet.render_arithmetic(a, "revenue")


def test_quarter_boundaries():
    assert packet.quarter_of(date(2026, 3, 31)) == "Q1 2026"
    assert packet.quarter_of(date(2026, 4, 1)) == "Q2 2026"
    assert packet.quarter_of(date(2026, 12, 31)) == "Q4 2026"


# --- the driving fact ---------------------------------------------------------


def test_the_oldest_open_req_carries_the_argument():
    old, new = _fact(1, first_seen_days=80), _fact(2, first_seen_days=10)
    assert packet.pick_driving_fact([new, old])["id"] == 1


def test_stale_and_closed_facts_are_excluded_from_the_arithmetic():
    # §3: stale evidence is EXCLUDED, not annotated. "Open 56 days" about a req
    # that came down is checkable in one click — R19.
    stale = _fact(1, first_seen_days=80, last_seen_days=20, freshness="stale")
    closed = _fact(2, first_seen_days=90, freshness="closed")
    assert packet.pick_driving_fact([stale, closed]) is None


def test_ageing_evidence_still_drives_the_arithmetic():
    ageing = _fact(1, last_seen_days=10, freshness="ageing")
    assert packet.pick_driving_fact([ageing])["id"] == 1


def test_non_open_role_facts_never_drive_it():
    assert packet.pick_driving_fact([_fact(kind="ic_hire")]) is None


# --- substitution -------------------------------------------------------------


def test_auto_values_resolve_from_target_and_evidence():
    values = packet.build_auto_values(TARGET, _fact(), SENDER, today=TODAY)
    assert values["First Name"] == "Marcus"
    assert values["Company Name"] == "Cadence Health"
    assert values["Role Title"] == "VP Revenue"
    assert values["function"] == "revenue" and values["Function"] == "Revenue"
    assert values["Your Name"] == "Barry"
    assert values["X"] == "8"          # 56 days floored to whole weeks


def test_missing_sources_are_omitted_rather_than_guessed():
    # No fallback: a plausible substitute for a missing first name is how a
    # confident, wrong email gets sent.
    bare = {**TARGET, "contact_first_name": None, "function": None}
    values = packet.build_auto_values(bare, None, SENDER, today=TODAY)
    assert "First Name" not in values
    assert "function" not in values and "Function" not in values
    assert "Role Title" not in values


def test_substitute_reports_unfilled_tokens_in_reading_order():
    filled, missing = packet.substitute(
        "[Company Name] needs [Client 1] and [Client 2]", {"Company Name": "Acme"}
    )
    assert filled.startswith("Acme needs")
    assert missing == ("Client 1", "Client 2")


# --- ready: the R1 / R19 guard ------------------------------------------------


def test_a_fully_resolved_packet_is_ready():
    p = _assemble([_fact()])
    assert p.ready is True
    assert p.unresolved_slots == () and p.blockers == ()
    assert "Marcus" in p.body_filled and "VP Revenue" in p.subject_line
    assert p.failure_mode.startswith("Positioning")


def test_an_unresolved_operator_slot_blocks():
    template = selector.Template(
        code="t10", title="T", section="S", when_to_use="",
        subject="Subject", body="Hi [First Name], see [Client 1].",
        failure_mode="fm",
    )
    p = _assemble([_fact()], template=template)
    assert p.ready is False
    assert "Client 1" in p.unresolved_slots
    assert any("unresolved slot" in b for b in p.blockers)


def test_an_unresolved_auto_slot_also_blocks():
    # §7 names only operator slots, but an unfilled "[First Name]" lands in the
    # greeting — the same failure with a worse blast radius.
    p = _assemble([_fact()], target={**TARGET, "contact_first_name": None})
    assert p.ready is False and "First Name" in p.unresolved_slots


def test_an_open_observed_slot_does_not_block():
    # It is the Gate 2 sentence. A packet with its observation open is working.
    template = selector.Template(
        code="t10", title="T", section="S", when_to_use="",
        subject="Subject",
        body="Hi [First Name], [New input: something you noticed].",
        failure_mode="fm",
    )
    p = _assemble([_fact()], template=template)
    assert p.ready is True and p.unresolved_slots == ()


def test_stale_driving_evidence_blocks_even_when_every_slot_is_filled():
    # R19 in full: nothing visibly wrong, and the claim is false.
    stale = _fact(freshness="stale", last_seen_days=20)
    template = selector.Template(
        code="t10", title="T", section="S", when_to_use="",
        subject="Subject", body="Hi [First Name].", failure_mode="fm",
    )
    p = _assemble([stale], template=template)
    assert p.ready is False
    assert any("stale or closed" in b for b in p.blockers)


def test_no_evidence_at_all_does_not_trip_the_stale_guard():
    # A target with no open-role evidence is not making a false claim about one;
    # it simply has no arithmetic. Conflating the two would block every packet
    # for a target whose board has no reqs.
    template = selector.Template(
        code="t10", title="T", section="S", when_to_use="",
        subject="Subject", body="Hi [First Name].", failure_mode="fm",
    )
    p = _assemble([], template=template)
    assert p.ready is True
    assert p.arithmetic["has_arithmetic"] is False


def test_packet_carries_evidence_ids_and_staleness():
    p = _assemble([_fact(1, last_seen_days=0), _fact(2, last_seen_days=9,
                                                    freshness="ageing")])
    assert p.evidence_ids == (1, 2)
    assert p.staleness_days == 9      # the oldest displayed fact (R19)


def test_a_missing_template_fails_loudly():
    with pytest.raises(KeyError, match="diverged"):
        packet.assemble_packet(
            {"id": 1, "template_code": "does-not-exist", "slot": 1},
            TARGET, [], today=TODAY, templates={}, config={},
            arithmetic_config=ARITHMETIC, sender=SENDER,
        )


# --- the no-LLM property -------------------------------------------------------


def test_packet_assembly_imports_no_provider_sdk():
    # A designed property, not an economy: assembly cannot fail from a provider
    # outage and cannot be prompt-injected, because nothing here generates text.
    source = (packet.__file__)
    text = open(source, encoding="utf-8").read()
    for forbidden in ("anthropic", "genai", "call_gemini", "call_anthropic",
                      "agent_run", "GRAPH_COMPLETION"):
        assert forbidden not in text, f"packet assembly must not reference {forbidden}"

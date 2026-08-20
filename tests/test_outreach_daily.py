"""Unit tests for the outreach daily loop (Track O, `35-` §14).

The SQL is exercised end-to-end against real Postgres in the scratchpad check;
here: the pure formatting, the drain's transition rules, and the failure posture.

What must hold:

  * **The drain never parks a target the operator has not answered for.** §8's
    friction is the whole point — an unanswered "what stalled it?" costs a
    capacity slot, which is what makes it get answered.
  * **One bad packet does not cost the others their morning.**
  * **The briefing line disappears rather than showing zeros.**
"""

from __future__ import annotations

from datetime import date

from agents.outreach import daily

TODAY = date(2026, 8, 17)

COUNTS = {
    "touches_due": 2, "cold_live": 3, "cold_ceiling": 15,
    "cards_open": 1, "targets_with_ageing_evidence": 0,
}


# --- the briefing line (§9: "one line and a link") ---------------------------


def test_line_reports_due_live_and_open_cards():
    line = daily.format_briefing_line(COUNTS, not_ready=0)
    assert "2 touch(es) due" in line and "3/15 live" in line
    assert "1 card(s) awaiting a decision" in line


def test_line_omits_clauses_that_are_zero():
    line = daily.format_briefing_line(COUNTS, not_ready=0)
    assert "not ready" not in line and "ageing" not in line


def test_line_includes_not_ready_and_ageing_when_present():
    line = daily.format_briefing_line(
        {**COUNTS, "targets_with_ageing_evidence": 2}, not_ready=1
    )
    assert "1 not ready" in line and "2 with ageing evidence" in line


def test_line_is_empty_when_nothing_is_live():
    # The briefing omits the section rather than printing a row of zeros — same
    # pattern as the reading-recs and new-prospects sections.
    quiet = {"touches_due": 0, "cold_live": 0, "cold_ceiling": 15,
             "cards_open": 0, "targets_with_ageing_evidence": 0}
    assert daily.format_briefing_line(quiet, not_ready=0) == ""


def test_an_open_card_alone_reports_only_the_card():
    # The real state on 2026-08-17: one card awaiting a decision, nothing live.
    # An earlier version padded this with "0 touch(es) due · 0/15 live", which is
    # two zeros of noise around the one thing worth saying. Caught by real data,
    # not by a test — the fixture had cards_open=0.
    just_a_card = {"touches_due": 0, "cold_live": 0, "cold_ceiling": 15,
                   "cards_open": 1, "targets_with_ageing_evidence": 0}
    line = daily.format_briefing_line(just_a_card, not_ready=0)
    assert line == "🎯 **Outreach:** 1 card(s) awaiting a decision"
    assert "0 touch" not in line and "0/15" not in line


def test_ageing_evidence_alone_still_reports():
    # This test previously asserted the opposite, matching a bug: the old guard
    # suppressed the whole line unless something was due, live, or carded.
    #
    # Ageing evidence with nothing live is worth saying precisely because of what
    # it usually means — the evidence loop has stopped. That is the failure that
    # cost a night of polling on 2026-08-15, and it was invisible because nothing
    # surfaced it. Ted's "evidence loop silent >48h" alert (§14) is the proper
    # version; until it exists this line is the cheap early warning.
    ageing_only = {"touches_due": 0, "cold_live": 0, "cold_ceiling": 15,
                   "cards_open": 0, "targets_with_ageing_evidence": 5}
    line = daily.format_briefing_line(ageing_only, not_ready=0)
    assert line == "🎯 **Outreach:** 5 with ageing evidence"


# --- drain reason provenance ---------------------------------------------------


def test_a_drain_written_reason_is_distinguishable_from_the_operators():
    assert daily.is_drain_reason(f"{daily.DRAIN_REASON_PREFIX} no reply") is True
    assert daily.is_drain_reason("they hired internally") is False
    assert daily.is_drain_reason(None) is False


# --- the drain rule (§8) -------------------------------------------------------


def _conn(mocker, drainable):
    cur = mocker.MagicMock()
    cur.fetchall.return_value = drainable
    cur.rowcount = 1
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


DRAINABLE = {"id": 7, "company_name": "Cadence Health",
             "stalled_reason": "they hired internally",
             "last_window": date(2026, 7, 1), "days_past": 47}


def test_a_drainable_target_with_a_reason_moves_to_the_watchlist(mocker):
    conn, cur = _conn(mocker, [DRAINABLE])
    result = daily.run_drain(conn, today=TODAY)
    assert result["drained"] == ["Cadence Health"] and result["awaiting_reason"] == []
    sql = [str(c.args[0]) for c in cur.execute.call_args_list if "UPDATE" in str(c.args[0])]
    assert sql and "status = 'watchlist'" in sql[0]
    # Guarded, so a reply landing mid-run cannot be overwritten by the drain.
    assert "status = 'in_sequence'" in sql[0]
    assert "watch_until" in sql[0]          # the constraint requires it


def test_a_drainable_target_without_a_reason_keeps_its_slot(mocker):
    # §8's deliberate friction. Parking it silently would make the capacity cap
    # stop measuring real attention, which is the one thing it exists to do.
    conn, cur = _conn(mocker, [{**DRAINABLE, "stalled_reason": None}])
    result = daily.run_drain(conn, today=TODAY)
    assert result["awaiting_reason"] == ["Cadence Health"] and result["drained"] == []
    assert not any("UPDATE" in str(c.args[0]) for c in cur.execute.call_args_list)


def test_dry_run_reports_without_writing(mocker):
    conn, cur = _conn(mocker, [DRAINABLE])
    result = daily.run_drain(conn, today=TODAY, dry_run=True)
    assert result["drained"] == ["Cadence Health"]
    assert not any("UPDATE" in str(c.args[0]) for c in cur.execute.call_args_list)


def test_nothing_drainable_is_a_clean_noop(mocker):
    conn, _ = _conn(mocker, [])
    assert daily.run_drain(conn, today=TODAY) == {"drained": [], "awaiting_reason": []}


def test_a_lost_race_does_not_report_a_drain(mocker):
    # rowcount 0 means something else moved the target between the SELECT and the
    # UPDATE — a reply landing mid-run. It must not be reported as drained.
    conn, cur = _conn(mocker, [DRAINABLE])
    cur.rowcount = 0
    assert daily.run_drain(conn, today=TODAY)["drained"] == []


def test_the_grace_window_matches_the_spec():
    assert daily.DRAIN_GRACE_DAYS == 14


# --- packet regeneration resilience -------------------------------------------


def test_one_failing_packet_does_not_cost_the_others(mocker):
    due = [{"id": 1, "slot": 1, "template_code": "a", "company_name": "A"},
           {"id": 2, "slot": 2, "template_code": "b", "company_name": "B"}]
    cur = mocker.MagicMock()
    cur.fetchall.return_value = due
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    mocker.patch.object(packet_mod := daily.packet, "previous_subject", return_value=None)
    mocker.patch.object(
        packet_mod, "fetch_packet_inputs",
        side_effect=[RuntimeError("bad row"), ({}, {}, [])],
    )
    ok = mocker.MagicMock(ready=True, blockers=())
    mocker.patch.object(packet_mod, "assemble_packet", return_value=ok)
    save = mocker.patch.object(packet_mod, "save_packet")

    result = daily.regenerate_packets(conn, today=TODAY)

    assert result == {"due": 2, "built": 1, "ready": 1, "failed": 1}
    save.assert_called_once()


def test_an_unready_packet_is_still_saved_and_counted(mocker):
    # It is saved deliberately: the operator needs to see WHY it is not ready,
    # and `ready` is what the send-guard reads. Withholding it would hide the
    # blocker instead of surfacing it.
    due = [{"id": 1, "slot": 1, "template_code": "a", "company_name": "A"}]
    cur = mocker.MagicMock()
    cur.fetchall.return_value = due
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur

    mocker.patch.object(daily.packet, "previous_subject", return_value=None)
    mocker.patch.object(daily.packet, "fetch_packet_inputs", return_value=({}, {}, []))
    blocked = mocker.MagicMock(ready=False, blockers=("unresolved slot(s): Client 1",))
    mocker.patch.object(daily.packet, "assemble_packet", return_value=blocked)
    save = mocker.patch.object(daily.packet, "save_packet")

    result = daily.regenerate_packets(conn, today=TODAY)
    assert result == {"due": 1, "built": 1, "ready": 0, "failed": 0}
    save.assert_called_once()


def test_the_briefing_counts_gate_zero_after_the_intake_cards():
    """Part 0. A triage queue is not a decision that ages, so it reads after the
    Gate 1 cards — the ones that actually hold up the pipeline."""
    from agents.outreach import daily
    line = daily.format_briefing_line({
        "touches_due": 0, "cold_live": 0, "cold_ceiling": 15,
        "cards_open": 1, "targets_with_ageing_evidence": 0,
        "awaiting_review": 25,
    }, not_ready=0)
    assert "25 to review" in line
    assert line.index("card(s) awaiting a decision") < line.index("25 to review")


def test_an_empty_gate_zero_queue_adds_no_clause():
    """The line's hard rule: a clause reading 'nothing happened' is worse than
    absent (eval UX-1's message budget)."""
    from agents.outreach import daily
    assert daily.format_briefing_line({
        "touches_due": 0, "cold_live": 0, "cold_ceiling": 15, "cards_open": 0,
        "targets_with_ageing_evidence": 0, "awaiting_review": 0,
    }, not_ready=0) == ""


def test_the_briefing_survives_a_counts_dict_without_gate_zero():
    """Defensive: `.get` rather than `[]`, so an older caller cannot KeyError."""
    from agents.outreach import daily
    line = daily.format_briefing_line({
        "touches_due": 2, "cold_live": 0, "cold_ceiling": 15, "cards_open": 0,
        "targets_with_ageing_evidence": 0,
    }, not_ready=0)
    assert "2 touch(es) due" in line

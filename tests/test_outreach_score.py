"""Unit tests for the scoring CLI (Track O, `cli/outreach_score.py`).

The tool records judgement; it never makes it. What must hold:

  * only 1/3/5 and the three real function states are accepted — the CHECK
    constraint says the same thing, but a typo should fail with a usable message
    rather than a constraint violation;
  * setting S4 or S5 stamps `signals_observed_at`, because that is what starts
    the 30-day re-check clock those two signals are measured against (§4);
  * a partial rubric reads as unscored, never as a low score.
"""

from __future__ import annotations

import pytest

from cli import outreach_score as score

ROW = {
    "id": 7, "company_name": "Cadence Health", "score": 23, "treatment": "work",
    "function_state": "vacant_seat", "compound_signal": True,
    "s2_stage_fit": 5, "s3_sector_match": 5,
    "s4_leadership_gap": 5, "s5_team_build_below": 5,
}


_UNSET = object()   # so `fetch=None` means "the row is missing", not "use the default"


def _conn(mocker, fetch=_UNSET):
    cur = mocker.MagicMock()
    cur.fetchone.return_value = ROW if fetch is _UNSET else fetch
    conn = mocker.MagicMock()
    conn.cursor.return_value.__enter__.return_value = cur
    return conn, cur


# --- the accepted vocabulary --------------------------------------------------


def test_only_the_rubrics_three_values_are_valid():
    assert score.VALID_SCORES == (1, 3, 5)


def test_only_the_three_real_function_states_are_valid():
    assert score.VALID_FUNCTION_STATES == ("self_covered", "under_led", "vacant_seat")


def test_all_four_operator_set_signals_are_covered():
    # S1 is derived and deliberately absent — outreach_s1() is authoritative and
    # this tool must not offer a way to override it (R6).
    assert set(score.SIGNALS) == {"s2", "s3", "s4", "s5"}
    assert not any(col.startswith("s1") for col, _ in score.SIGNALS.values())


# --- writing ------------------------------------------------------------------


def test_setting_s4_stamps_the_recheck_clock(mocker):
    conn, cur = _conn(mocker)
    score.apply_scores(conn, 7, {"s4_leadership_gap": 5})
    sql = cur.execute.call_args_list[0].args[0]
    assert "signals_observed_at = CURRENT_DATE" in sql


def test_setting_s5_stamps_it_too(mocker):
    conn, cur = _conn(mocker)
    score.apply_scores(conn, 7, {"s5_team_build_below": 3})
    assert "signals_observed_at" in cur.execute.call_args_list[0].args[0]


def test_setting_only_s2_does_not_stamp_it(mocker):
    # S2/S3 are "stored at intake, rarely refreshed" — they are not what the
    # 30-day cadence tracks, so they must not reset its clock.
    conn, cur = _conn(mocker)
    score.apply_scores(conn, 7, {"s2_stage_fit": 5})
    assert "signals_observed_at" not in cur.execute.call_args_list[0].args[0]


def test_function_state_alone_writes_without_touching_scores(mocker):
    conn, cur = _conn(mocker)
    score.apply_scores(conn, 7, {"function_state": "under_led"})
    sql, params = cur.execute.call_args_list[0].args
    assert "function_state = %(function_state)s" in sql
    assert params["function_state"] == "under_led"
    assert "signals_observed_at" not in sql


def test_an_unknown_target_raises(mocker):
    conn, cur = _conn(mocker, fetch=None)
    with pytest.raises(KeyError):
        score.apply_scores(conn, 999, {"s2_stage_fit": 5})


# --- the summary line ---------------------------------------------------------


def test_summary_shows_score_treatment_and_compound_marker():
    line = score.summarize(ROW)
    assert "Cadence Health" in line and "23/25" in line and "work" in line
    assert "vacant_seat" in line and "⚡" in line


def test_summary_of_a_partial_rubric_reads_as_unscored_not_low():
    partial = {**ROW, "score": None, "treatment": None, "compound_signal": False,
               "s4_leadership_gap": None, "s5_team_build_below": None}
    line = score.summarize(partial)
    assert "—/25" in line and "unscored" in line
    # And says exactly which are missing, so the next command is obvious.
    assert "needs: s4, s5" in line


def test_summary_flags_an_unset_function_state():
    line = score.summarize({**ROW, "function_state": None})
    assert "NOT SET" in line

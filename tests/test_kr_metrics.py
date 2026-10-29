"""Unit tests for the KR metric registry and scoring (PRD-notion-integration.md N4).

Pure: windows, scoring, and the no_data path run against a fake cursor. The SQL
itself is checked against fixture rows in `test_kr_metrics_db.py`, which needs a
Postgres connection.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from agents._lib import kr_metrics as km
from agents.notion import okr_schema

TZ = timezone(timedelta(hours=-4))
D = Decimal


def at(y, m, d, h=0):
    return datetime(y, m, d, h, tzinfo=TZ)


def test_registry_matches_the_notion_metric_options():
    assert set(km.REGISTRY) == set(okr_schema.METRICS)


def test_every_computed_metric_has_sql_and_manual_has_none():
    for mid, m in km.REGISTRY.items():
        assert (m.sql is None) == (mid == "manual"), mid


# --- windows ------------------------------------------------------------------------


def test_quarter_bounds():
    assert km.quarter_bounds("2026-Q4") == (date(2026, 10, 1), date(2027, 1, 1))
    assert km.quarter_bounds("2027-Q1") == (date(2027, 1, 1), date(2027, 4, 1))
    assert km.quarter_of(date(2026, 11, 3)) == "2026-Q4"


def test_month_window_at_midnight_on_the_first_reports_the_full_previous_month():
    start, first, end = km.window("llm_spend_month", "2026-Q4", at(2026, 11, 1))
    assert (first, end) == (date(2026, 10, 1), date(2026, 11, 1))
    assert start == at(2026, 10, 1)


def test_rolling_window_is_relative_to_as_of():
    start, _, _ = km.window("agent_run_success_rate", "2026-Q4", at(2026, 11, 2))
    assert start == at(2026, 10, 26)


def test_clamp_stops_quarter_metrics_at_the_quarter_end():
    assert km.clamp("engagements_signed", "2026-Q4", at(2027, 1, 4)) == at(2027, 1, 1)
    assert km.clamp("llm_spend_month", "2026-Q4", at(2027, 1, 4)) == at(2027, 1, 4)


def test_day_of_window_counts_the_day_just_ended_at_midnight():
    assert km.day_of_window(date(2026, 10, 1), at(2026, 10, 15)) == 14
    assert km.day_of_window(date(2026, 10, 1), at(2026, 10, 16)) == 15


def test_elapsed_share():
    q = (date(2026, 10, 1), date(2027, 1, 1))  # 92 days
    assert km.elapsed_share(*q, at(2026, 10, 1)) == pytest.approx(1 / 92)  # floor of 1 day
    assert km.elapsed_share(*q, at(2026, 11, 16)) == pytest.approx(46 / 92)
    assert km.elapsed_share(*q, at(2027, 2, 1)) == 1.0


# --- compute: no_data ------------------------------------------------------------------


class _Conn:
    def __init__(self, row):
        self.row, self.calls = row, []

    def cursor(self):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params):
        self.calls.append((sql, params))

    def fetchone(self):
        return self.row


@pytest.mark.parametrize("row", [(None, 0), (D("0"), 0)])
def test_compute_returns_none_when_nothing_was_measured(row):
    conn = _Conn(row)
    assert km.compute(conn, "median_engagement_value", quarter="2026-Q4",
                      as_of=at(2026, 11, 2)) is None


def test_count_and_sum_queries_always_measure_so_zero_is_data():
    for mid in ("engagements_signed", "cold_sequences_live", "sequences_completed",
                "conversations_opened", "calls_booked", "proposals_sent", "llm_spend_month"):
        sql = km.REGISTRY[mid].sql
        assert ", 1 FROM" in sql or "\n               1" in sql, mid
    assert km.compute(_Conn((0, 1)), "proposals_sent", quarter="2026-Q4",
                      as_of=at(2026, 11, 2)) == 0


def test_compute_passes_the_window():
    conn = _Conn((D("91.5"), 40))
    assert km.compute(conn, "agent_run_success_rate", quarter="2026-Q4",
                      as_of=at(2026, 11, 2)) == D("91.50")
    params = conn.calls[0][1]
    assert params["start"] == at(2026, 10, 26) and params["as_of"] == at(2026, 11, 2)


def test_manual_metric_uses_the_manual_value_and_runs_no_sql():
    conn = _Conn(None)
    assert km.compute(conn, "manual", quarter="2026-Q4", as_of=at(2026, 11, 2),
                      manual_value=D("12")) == 12
    assert km.compute(conn, "manual", quarter="2026-Q4", as_of=at(2026, 11, 2)) is None
    assert conn.calls == []


# --- scoring --------------------------------------------------------------------------


def test_no_data_and_set_target():
    assert km.score("manual", km.HIGHER, None, D(5)).status == km.NO_DATA
    s = km.score("proposals_sent", km.HIGHER, D(4), None)
    assert s.status == km.SET_TARGET and s.progress is None


@pytest.mark.parametrize("current,status", [(D(95), km.ON_TRACK), (D(86), km.AT_RISK),
                                            (D(85), km.OFF_TRACK)])
def test_point_higher_thresholds(current, status):
    assert km.score("agent_run_success_rate", km.HIGHER, current, D(95)).status in (status,)


def test_point_at_risk_floor_is_0_9():
    assert km.score("touches_on_schedule_rate", km.HIGHER, D(81), D(90)).status == km.AT_RISK
    assert km.score("touches_on_schedule_rate", km.HIGHER, D(80), D(90)).status == km.OFF_TRACK


def test_cumulative_higher_is_prorated_with_0_7_floor():
    # Half the quarter gone, target 40 → 20 due. 14/20 = 0.7.
    def status(current):
        return km.score("sequences_completed", km.HIGHER, D(current), D(40), share=0.5).status
    assert (status(20), status(14), status(13)) == (km.ON_TRACK, km.AT_RISK, km.OFF_TRACK)


def test_too_early_on_day_14_but_not_day_15():
    early = km.score("engagements_signed", km.HIGHER, D(0), D(3), share=14 / 92, day=14)
    late = km.score("engagements_signed", km.HIGHER, D(0), D(3), share=15 / 92, day=15)
    assert early.status == km.TOO_EARLY
    assert late.status == km.OFF_TRACK


def test_too_early_never_hides_on_track_or_applies_to_point_kpis():
    assert km.score("engagements_signed", km.HIGHER, D(3), D(3), share=0.1, day=3).status \
        == km.ON_TRACK
    assert km.score("agent_run_success_rate", km.HIGHER, D(50), D(95), day=3).status \
        == km.OFF_TRACK


def test_cumulative_lower_is_better_uses_the_prorated_budget():
    # Budget $60, a third of the month gone → $20 allowed.
    on = km.score("llm_spend_month", km.LOWER, D(20), D(60), share=1 / 3, day=10)
    risk = km.score("llm_spend_month", km.LOWER, D(25), D(60), share=1 / 3, day=10)
    off = km.score("llm_spend_month", km.LOWER, D(30), D(60), share=1 / 3, day=10)
    assert (on.status, risk.status, off.status) == (km.ON_TRACK, km.AT_RISK, km.OFF_TRACK)
    assert on.progress is None  # no bar for Lower is better


def test_point_lower_is_better_and_zero_current():
    assert km.score("manual", km.LOWER, D(30), D(24)).status == km.OFF_TRACK  # 0.8
    assert km.score("manual", km.LOWER, D(26), D(24)).status == km.AT_RISK  # 0.92
    zero = km.score("manual", km.LOWER, D(0), D(24))
    assert zero.status == km.ON_TRACK and zero.progress is None


def test_progress_caps_at_one():
    assert km.score("proposals_sent", km.HIGHER, D(12), D(10)).progress == 1.0
    assert km.score("proposals_sent", km.HIGHER, D(5), D(10), share=0.5).progress == 0.5


def test_worst_prefers_off_track_with_lowest_score():
    def r(i, status, s):
        return km.Result(i, f"kr{i}", "manual", D(1), D(1), km.Score(status, s, None))
    results = [r(1, km.AT_RISK, 0.75), r(2, km.OFF_TRACK, 0.5), r(3, km.OFF_TRACK, 0.2)]
    assert km.worst(results).key_result_id == 3
    assert km.worst([r(1, km.AT_RISK, 0.95), r(4, km.AT_RISK, 0.8)]).key_result_id == 4
    assert km.worst([r(1, km.ON_TRACK, 2.0)]) is None
